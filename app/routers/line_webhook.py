"""LINE webhook 路由 — 規格 1.2、1.7:簽章驗證、僅處理 1:1 私訊、事件去重、follow/unfollow、記帳寫入、
刪除上一筆、查詢庫存/損益。

_handle_text_message() 完整流程:
  1. 未連結 → OAuth 授權 URL
  2. needs_reauth → 重新授權 URL
  3. 「❌ 刪除上一筆」→ 刪除上一次記帳(5 分鐘內有效)
  4. 「查詢」→ 回覆目前庫存/損益摘要
  5. 「立即同步」→ 重新計算並寫回試算表狀態欄
  6. 「新增分頁 <名稱>」(或舊說法「新增帳戶」)→ 建立新帳戶分頁
  7. 多帳戶未標籤 → Quick Reply 詢問帳戶(5 分鐘有效期)
  8. 已連結(含 Quick Reply 選擇回應) → parse → fuzzy match → 賣超防呆 → 寫入試算表 → 回覆(含刪除 Quick Reply)
"""

import time
import uuid
from collections import defaultdict
from datetime import date as Date
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, Request
from googleapiclient.errors import HttpError
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    ImageMessage,
    MessageAction as LineMessageAction,
    MessagingApi,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import (
    FollowEvent,
    MessageEvent,
    TextMessageContent,
    UnfollowEvent,
    UserSource,
)

from app.config import get_settings
from app.models.schemas import (
    FriendRecord,
    FriendStatus,
    Position,
    ResyncResult,
    StockQuote,
    TransactionAction,
    TransactionRow,
)
from app.routers.tick import get_cached_stock_list
from app.services.friend_repository import deactivate_friend, get_friend_record, reactivate_friend
from app.services.fuzzy_match import resolve_stock
from app.services.oauth_service import OAuthInvalidGrantError
from app.services.parser import parse_transaction_text
from app.services.pnl_engine import InsufficientPositionError, apply_transaction, compute_unrealized_pnl
from app.services.sheets_client import (
    append_transaction_rows,
    create_account_tab,
    delete_transaction_rows,
    read_all_account_positions,
    read_tab_positions,
    resync,
)

router = APIRouter()

# 短時間窗口去重(5~10 分鐘),而非永久記錄事件 ID — ADR-015。
_DEDUPE_WINDOW_SECONDS = 600
_recent_event_ids: dict[str, float] = {}

# 多帳戶 Quick Reply 選擇的暫存狀態(process 記憶體,5 分鐘有效)
_PENDING_SELECTION_WINDOW = 300
_pending_selections: dict[str, dict] = {}

# 刪除上一筆記帳的暫存狀態(process 記憶體,5 分鐘有效)
_PENDING_UNDO_WINDOW = 300
_pending_undo: dict[str, dict] = {}


# ── 事件去重 ──────────────────────────────────────────────────────────────────

def _is_duplicate_event(webhook_event_id: str) -> bool:
    now = time.monotonic()
    expired = [
        eid for eid, seen_at in _recent_event_ids.items() if now - seen_at > _DEDUPE_WINDOW_SECONDS
    ]
    for eid in expired:
        del _recent_event_ids[eid]
    if webhook_event_id in _recent_event_ids:
        return True
    _recent_event_ids[webhook_event_id] = now
    return False


# ── Quick Reply 帳戶選擇暫存 ──────────────────────────────────────────────────

def _cleanup_pending_selections() -> None:
    now = time.monotonic()
    expired = [uid for uid, p in _pending_selections.items() if now > p["expires_at"]]
    for uid in expired:
        del _pending_selections[uid]


def _get_pending(line_user_id: str) -> dict | None:
    _cleanup_pending_selections()
    return _pending_selections.get(line_user_id)


def _set_pending(line_user_id: str, transactions: list, tabs: list[str]) -> None:
    _pending_selections[line_user_id] = {
        "transactions": transactions,
        "tabs": tabs,
        "expires_at": time.monotonic() + _PENDING_SELECTION_WINDOW,
    }


def _clear_pending(line_user_id: str) -> None:
    _pending_selections.pop(line_user_id, None)


# ── 刪除暫存 ──────────────────────────────────────────────────────────────────

def _get_undo(line_user_id: str) -> dict | None:
    entry = _pending_undo.get(line_user_id)
    if entry is None:
        return None
    if time.monotonic() > entry["expires_at"]:
        del _pending_undo[line_user_id]
        return None
    return entry


def _set_undo(line_user_id: str, written_rows: list[tuple[str, str]]) -> None:
    _pending_undo[line_user_id] = {
        "written_rows": written_rows,
        "expires_at": time.monotonic() + _PENDING_UNDO_WINDOW,
    }


def _clear_undo(line_user_id: str) -> None:
    _pending_undo.pop(line_user_id, None)


# ── LINE 回覆工具 ─────────────────────────────────────────────────────────────

def _liff_oauth_url() -> str:
    return f"https://liff.line.me/{get_settings().liff_id}"


def _liff_dashboard_url() -> str:
    settings = get_settings()
    if settings.liff_dashboard_url:
        return settings.liff_dashboard_url
    return f"https://liff.line.me/{settings.liff_dashboard_id}"


def _welcome_image_url() -> str | None:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/static/welcome.jpg" if base else None


def _reply_messages(reply_token: str, messages: list) -> None:
    settings = get_settings()
    configuration = Configuration(access_token=settings.line_channel_access_token)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


def _reply_text(reply_token: str, text: str) -> None:
    settings = get_settings()
    configuration = Configuration(access_token=settings.line_channel_access_token)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
        )


def _reply_with_quick_reply(reply_token: str, text: str, options: list[str]) -> None:
    settings = get_settings()
    configuration = Configuration(access_token=settings.line_channel_access_token)
    items = [QuickReplyItem(action=LineMessageAction(label=opt, text=opt)) for opt in options]
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text, quick_reply=QuickReply(items=items))],
            )
        )


# ── 記帳核心邏輯 ──────────────────────────────────────────────────────────────

def _book_transactions(
    friend: FriendRecord,
    tab_name: str,
    parsed_txns: list,
    stock_list: list[StockQuote],
) -> tuple[list[str], list[str], list[str]]:
    """fuzzy match → 讀取庫存 → 賣超防呆 → 逐筆寫入試算表。
    回傳 (成功訊息列表, 錯誤訊息列表, 已寫入的 row_uuid 列表)。
    OAuth 失效或 Sheets API 錯誤直接往上拋,呼叫端負責回覆親友。
    """
    successes: list[str] = []
    errors: list[str] = []
    written_uuids: list[str] = []

    resolved: list[tuple] = []
    for txn in parsed_txns:
        try:
            stock = resolve_stock(txn.stock_query, stock_list)
            resolved.append((txn, stock))
        except ValueError:
            errors.append(f"找不到股票「{txn.stock_query}」")

    if not resolved:
        return successes, errors, written_uuids

    positions: dict[str, Position] = read_tab_positions(friend, tab_name, stock_list)

    rows_to_write: list[TransactionRow] = []
    for txn, stock in resolved:
        position = positions.get(stock.code, Position(stock_code=stock.code))

        # 賣出只給金額（股數 None）= 賣掉目前全部持股，股數於此依當下庫存決定
        effective_txn = txn
        if txn.action is TransactionAction.SELL and txn.quantity is None:
            if position.quantity <= 0:
                errors.append(f"「{stock.code} {stock.name}」目前無持股,無法賣出,這筆略過")
                continue
            effective_txn = txn.model_copy(update={"quantity": position.quantity})

        try:
            new_position = apply_transaction(position, effective_txn)
        except InsufficientPositionError:
            errors.append(f"「{stock.code} {stock.name}」庫存不足({position.quantity} 股),這筆略過")
            continue

        row = TransactionRow(
            row_uuid=str(uuid.uuid4()),
            date=Date.today(),
            action=effective_txn.action,
            stock_query=f"{stock.code} {stock.name}",
            quantity=effective_txn.quantity,
            amount=effective_txn.amount,
        )
        rows_to_write.append(row)
        positions[stock.code] = new_position
        written_uuids.append(row.row_uuid)

        label_parts = [effective_txn.action.value, f"{stock.code} {stock.name}"]
        if effective_txn.quantity is not None:
            label_parts.append(f"{effective_txn.quantity} 股")
        if effective_txn.amount is not None:
            label_parts.append(f"${effective_txn.amount}")
        successes.append(" ".join(label_parts))

    # 多筆一次寫入：一次算好起始列再整批寫，避免逐筆「讀列號→寫入」因 Sheets
    # 寫入傳播延遲而算到同一列、互相覆蓋（首次冷啟動時最容易發生）。
    if rows_to_write:
        append_transaction_rows(friend, tab_name, rows_to_write)

    return successes, errors, written_uuids


def _format_booking_reply(successes: list[str], errors: list[str]) -> str:
    parts = []
    if successes:
        parts.append("✅ 記帳成功:\n" + "\n".join(f"• {s}" for s in successes))
    if errors:
        parts.append("⚠️ 以下略過:\n" + "\n".join(f"• {e}" for e in errors))
    return "\n\n".join(parts) or "沒有任何記帳成功"


def _execute_booking(
    reply_token: str,
    friend: FriendRecord,
    transactions: list,
    tab_name: str,
    stock_list: list[StockQuote],
    *,
    extra_errors: list[str] | None = None,
) -> None:
    """呼叫 _book_transactions,處理 OAuth/HTTP 錯誤後回覆親友"""
    try:
        successes, errors, written_uuids = _book_transactions(friend, tab_name, transactions, stock_list)
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結才能繼續記帳:{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{_liff_oauth_url()}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    if extra_errors:
        errors = extra_errors + errors
    reply_text = _format_booking_reply(successes, errors)
    if successes:
        _set_undo(friend.line_user_id, [(tab_name, uid) for uid in written_uuids])
        _reply_with_quick_reply(reply_token, reply_text, ["❌ 刪除上一筆"])
    else:
        _reply_text(reply_token, reply_text)


def _execute_booking_by_tag(
    reply_token: str,
    friend: FriendRecord,
    transactions: list,
    stock_list: list[StockQuote],
    *,
    extra_errors: list[str] | None = None,
) -> None:
    """多帳戶且全部已標籤:依 account_tag 分組各自記帳,合併成一則回覆"""
    by_tab: dict[str, list] = defaultdict(list)
    for txn in transactions:
        by_tab[txn.account_tag].append(txn)

    all_successes: list[str] = []
    all_errors: list[str] = []
    all_written_rows: list[tuple[str, str]] = []
    try:
        for tab, txns in by_tab.items():
            s, e, uuids = _book_transactions(friend, tab, txns, stock_list)
            all_successes.extend(s)
            all_errors.extend(e)
            all_written_rows.extend((tab, uid) for uid in uuids)
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結才能繼續記帳:{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{_liff_oauth_url()}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    if extra_errors:
        all_errors = extra_errors + all_errors
    reply_text = _format_booking_reply(all_successes, all_errors)
    if all_successes:
        _set_undo(friend.line_user_id, all_written_rows)
        _reply_with_quick_reply(reply_token, reply_text, ["❌ 刪除上一筆"])
    else:
        _reply_text(reply_token, reply_text)


# ── 刪除與查詢 ────────────────────────────────────────────────────────────────

def _handle_undo(reply_token: str, friend: FriendRecord) -> None:
    """刪除上一筆記帳——規格 1.7.1"""
    _clear_pending(friend.line_user_id)
    undo_info = _get_undo(friend.line_user_id)
    if undo_info is None:
        _reply_text(reply_token, "刪除時效已過(5 分鐘),無法刪除")
        return

    _clear_undo(friend.line_user_id)
    try:
        deleted = delete_transaction_rows(friend, undo_info["written_rows"])
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結:{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{_liff_oauth_url()}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    if deleted > 0:
        _reply_text(reply_token, f"✅ 已刪除 {deleted} 筆記帳")
    else:
        _reply_text(reply_token, "找不到要刪除的紀錄,可能已被手動刪除")


def _format_query_reply(
    result: ResyncResult,
    name_map: dict[str, str],
    price_map: dict[str, Decimal | None],
) -> str:
    if not result.accounts:
        return "目前沒有帳戶分頁,請確認試算表結構"

    parts: list[str] = []
    for account in result.accounts:
        active = [p for p in account.positions if p.quantity > 0]
        section: list[str] = [f"📊 {account.tab_name}"]

        total_realized = Decimal("0")
        total_market_value = Decimal("0")
        total_cost_basis = Decimal("0")
        has_price = False

        if not active:
            section.append("目前無持股")
        else:
            for pos in active:
                name = name_map.get(pos.stock_code, pos.stock_code)
                section.append(f"\n{pos.stock_code} {name}  {pos.quantity}股")
                price = price_map.get(pos.stock_code)
                if price is not None:
                    unrealized = compute_unrealized_pnl(pos, price)
                    sign = "+" if unrealized >= 0 else ""
                    section.append(f"均價 ${pos.avg_cost:.2f} | 未實現 {sign}{unrealized:,.0f}")
                    total_market_value += pos.quantity * price
                    has_price = True
                else:
                    section.append(f"均價 ${pos.avg_cost:.2f} | 無即時報價")
                total_realized += pos.realized_pnl
                total_cost_basis += pos.quantity * pos.avg_cost
        section.append("────────────")
        sign = "+" if total_realized >= 0 else ""
        section.append(f"投入本金 ${total_cost_basis:,.0f}")
        section.append(f"已實現損益 {sign}{total_realized:,.0f}")
        if has_price:
            section.append(f"持股市值 ${total_market_value:,.0f}")
        section.append("────────────")
        parts.append("\n".join(section))

    parts.append(f"📊 查看圖表儀表板\n{_liff_dashboard_url()}")
    return "\n".join(parts)


def _handle_query(reply_token: str, friend: FriendRecord) -> None:
    """查詢目前庫存與損益——規格 1.7.2"""
    _clear_pending(friend.line_user_id)
    stock_list = get_cached_stock_list()
    try:
        # 查詢只是「看」,走只讀路徑不回寫試算表(回寫留給記帳/刪除/每日 tick)——成本與延遲優化
        result = read_all_account_positions(friend, stock_list)
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結:{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{_liff_oauth_url()}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    name_map = {q.code: q.name for q in stock_list}
    price_map: dict[str, Decimal | None] = {q.code: q.close for q in stock_list}
    _reply_text(reply_token, _format_query_reply(result, name_map, price_map))


def _handle_sync(reply_token: str, friend: FriendRecord) -> None:
    """立即同步：重新計算試算表損益並寫回狀態欄"""
    _clear_pending(friend.line_user_id)
    stock_list = get_cached_stock_list()
    try:
        resync(friend, stock_list)
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期，需要重新連結：{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            _reply_text(reply_token, f"找不到試算表（可能已被刪除），需要重新連結：{_liff_oauth_url()}")
        else:
            _reply_text(reply_token, "同步失敗，請稍後再試")
        return
    _reply_text(reply_token, "✅ 同步完成！試算表狀態欄已更新。")


def _handle_add_tab(reply_token: str, friend: FriendRecord, tab_name: str) -> None:
    """新增帳戶分頁：建立符合規格的新分頁並套用標準格式"""
    try:
        create_account_tab(friend, tab_name)
    except OAuthInvalidGrantError:
        _reply_text(reply_token, f"試算表授權已過期，需要重新連結：{_liff_oauth_url()}")
        return
    except HttpError as exc:
        if exc.resp.status == 400:
            _reply_text(reply_token, f"「{tab_name}」無法使用（名稱重複或含不允許的字元），請換一個名稱")
        else:
            _reply_text(reply_token, "新增分頁失敗，請稍後再試")
        return
    _reply_text(
        reply_token,
        f"✅ 已新增帳戶分頁「{tab_name}」！\n多帳戶記帳時，可在開頭加帳戶標籤（例如：{tab_name}/買 台積電 100 85000）直接寫入此分頁。",
    )


# ── 說明文字 ──────────────────────────────────────────────────────────────────

_DISCLAIMER = "⚠️ 本工具僅供個人記帳參考，非正式對帳或報稅依據。"

_FORMAT_GUIDE = (
    "記帳格式（請用空白間隔）\n"
    "【買賣】買/賣 股票 股數 總金額\n"
    "【買賣】買/賣 股票 總金額 (不填股數也可)\n"
    "【股利】配息 股票 金額\n"
    "【配股】配股 股票 股數\n"
    "\n"
    "範例：\n"
    "買 台積電 100 8500\n"
    "賣 2330 50 4800\n"
    "配息 0050 3000\n"
    "配股 0056 100\n"
    "\n"
    "傳「查詢」可查看持股損益"
)


def _build_welcome_new_text(oauth_url: str) -> str:
    return (
        "嗨！我是你的記帳小幫手 📊\n"
        "\n"
        "首先，請點以下連結創建Google記帳試算表：\n"
        f"{oauth_url}\n"
        "\n"
        "連結完成後就可以開始記帳囉！\n"
        f"{_FORMAT_GUIDE}\n"
        "\n"
        "LINE選單中的「使用說明」，可在記帳前先點選確認唷😊\n"
        "\n"
        f"📊 查看圖表儀表板\n{_liff_dashboard_url()}\n"
        "\n"
        f"{_DISCLAIMER}"
    )


def _build_welcome_back_text() -> str:
    return (
        "歡迎回來！試算表還在，直接繼續記帳沒問題 📊\n"
        "\n"
        f"{_FORMAT_GUIDE}\n"
        "\n"
        "隨時可傳「使用說明」再看一次 😊"
    )


def _build_usage_guide_text() -> str:
    return (
        "📊 記帳格式\n"
        "【買賣】買/賣 股票 股數 總金額\n"
        "【買賣】買/賣 股票 總金額 (不填股數也可)\n"
        "【股利】配息 股票 金額\n"
        "【配股】配股 股票 股數\n"
        "請使用空白間隔區分辨識\n"
        "\n"
        "範例如下:(輸入股票代碼或名稱皆可辨識)\n"
        "【買賣】\n"
        "買 台積電 100 8500  (←輸入股票名稱)\n"
        "買 2330 100 8500  (←輸入股票代碼)\n"
        "賣 2330 50 4800  (←賣出, 改為「賣」即可)\n"
        "【配股配息】\n"
        "配息 0050 3000元\n"
        "配股 0056 100股\n"
        "\n"
        "輸入「查詢」即可查看持股損益\n"
        "可多筆分行輸入，一次傳送\n"
        "試算表的股票(股數)單位為「股」,若輸入1張,會自動辨識為1000股\n"
        "\n"
        "📂 多帳戶管理\n"
        "新增分頁 <分頁名稱>\n"
        "例如：新增分頁 海外帳戶\n"
        "可建立獨立帳戶分頁，分類記錄不同帳戶的持股\n"
        "\n"
        f"📊 查看圖表儀表板\n{_liff_dashboard_url()}\n"
        "\n"
        f"{_DISCLAIMER}"
    )


# ── 事件處理 ──────────────────────────────────────────────────────────────────

def _handle_follow_event(event: FollowEvent) -> None:
    line_user_id = event.source.user_id
    friend = get_friend_record(line_user_id)
    if friend is None:
        img_url = _welcome_image_url()
        messages = []
        if img_url:
            messages.append(ImageMessage(original_content_url=img_url, preview_image_url=img_url))
        messages.append(TextMessage(text=_build_welcome_new_text(_liff_oauth_url())))
        _reply_messages(event.reply_token, messages)
    elif friend.status == FriendStatus.INACTIVE:
        reactivate_friend(line_user_id)
        _reply_text(event.reply_token, _build_welcome_back_text())
    elif friend.status == FriendStatus.NEEDS_REAUTH:
        _reply_text(
            event.reply_token,
            f"歡迎回來！試算表授權已過期，需要重新連結才能繼續記帳：{_liff_oauth_url()}",
        )


def _handle_unfollow_event(event: UnfollowEvent) -> None:
    line_user_id = event.source.user_id
    if get_friend_record(line_user_id) is not None:
        deactivate_friend(line_user_id)


def _handle_text_message(event: MessageEvent) -> None:
    line_user_id = event.source.user_id
    text = event.message.text.strip()

    friend = get_friend_record(line_user_id)

    # 使用說明優先回覆，不論是否已連結——規格 1.8
    if text == "使用說明":
        guide = _build_usage_guide_text()
        if friend is None or friend.status == FriendStatus.NEEDS_REAUTH:
            guide += f"\n\n連結試算表：{_liff_oauth_url()}"
        _reply_text(event.reply_token, guide)
        return

    if friend is None:
        _reply_text(event.reply_token, f"尚未連結記帳試算表,請點這裡授權:{_liff_oauth_url()}")
        return

    if friend.status == FriendStatus.NEEDS_REAUTH:
        _reply_text(event.reply_token, f"試算表授權已過期,請重新連結就可以繼續記帳:{_liff_oauth_url()}")
        return

    # 特殊指令優先於記帳解析——規格 1.7
    if text == "❌ 刪除上一筆":
        _handle_undo(event.reply_token, friend)
        return
    if text == "查詢":
        _handle_query(event.reply_token, friend)
        return
    if text == "立即同步":
        _handle_sync(event.reply_token, friend)
        return
    # 「新增分頁」(使用說明採用的說法)與「新增帳戶」(舊說法)皆可觸發新增分頁
    for _add_tab_prefix in ("新增分頁", "新增帳戶"):
        if text.startswith(_add_tab_prefix):
            tab_name = text.removeprefix(_add_tab_prefix).strip()
            if not tab_name:
                _reply_text(event.reply_token, "請輸入分頁名稱，例如：新增分頁 海外帳戶")
                return
            _handle_add_tab(event.reply_token, friend, tab_name)
            return

    # 先檢查是否為多帳戶 Quick Reply 的帳戶選擇回應
    pending = _get_pending(line_user_id)
    if pending is not None:
        if text in pending["tabs"]:
            _clear_pending(line_user_id)
            stock_list = get_cached_stock_list()
            _execute_booking(event.reply_token, friend, pending["transactions"], text, stock_list)
            return
        _clear_pending(line_user_id)  # 新訊息取消前一次待選擇狀態

    # 解析記帳文字
    stock_list = get_cached_stock_list()
    closing_lookup = {q.code: q.close for q in stock_list}
    parse_result = parse_transaction_text(
        text, closing_price_lookup=lambda q: closing_lookup.get(q)
    )

    if not parse_result.transactions:
        error_lines = [
            f"第 {e.line_number} 行:「{e.raw_text}」— {e.reason}" for e in parse_result.errors
        ]
        _reply_text(event.reply_token, "讀取失敗:\n" + "\n".join(error_lines))
        return

    parse_errors = [
        f"第 {e.line_number} 行:「{e.raw_text}」— {e.reason}" for e in parse_result.errors
    ]

    tabs = friend.account_tabs_cache or []
    if not tabs:
        _reply_text(event.reply_token, "找不到帳戶分頁,請確認試算表結構是否正確,或稍後再試")
        return

    if len(tabs) == 1:
        _execute_booking(
            event.reply_token, friend, parse_result.transactions, tabs[0], stock_list,
            extra_errors=parse_errors,
        )
        return

    # 多帳戶:檢查所有交易是否都帶有效的帳戶標籤
    all_tagged = all(
        txn.account_tag and txn.account_tag in tabs for txn in parse_result.transactions
    )
    if all_tagged:
        _execute_booking_by_tag(
            event.reply_token, friend, parse_result.transactions, stock_list,
            extra_errors=parse_errors,
        )
    else:
        _set_pending(line_user_id, parse_result.transactions, tabs)
        prompt = "請問要記在哪個帳戶?"
        if parse_errors:
            prompt = "⚠️ 以下略過:\n" + "\n".join(f"• {e}" for e in parse_errors) + "\n\n" + prompt
        _reply_with_quick_reply(event.reply_token, prompt, tabs[:13])


@router.post("/line/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(...)) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    settings = get_settings()
    parser = WebhookParser(settings.line_channel_secret)
    try:
        events = parser.parse(body, x_line_signature)
    except InvalidSignatureError as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    for event in events:
        if not isinstance(event.source, UserSource):
            continue  # 群組/聊天室訊息一律忽略,不回應 — 規格 1.2
        if _is_duplicate_event(event.webhook_event_id):
            continue
        if isinstance(event, FollowEvent):
            _handle_follow_event(event)
        elif isinstance(event, UnfollowEvent):
            _handle_unfollow_event(event)
        elif isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            _handle_text_message(event)

    return {"status": "ok"}
