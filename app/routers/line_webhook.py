"""LINE webhook 路由 — 規格 1.2:簽章驗證、僅處理 1:1 私訊、事件去重、follow/unfollow、記帳寫入。

_handle_text_message() 完整流程:
  1. 未連結 → OAuth 授權 URL
  2. needs_reauth → 重新授權 URL
  3. 多帳戶未標籤 → Quick Reply 詢問帳戶(5 分鐘有效期)
  4. 已連結(含 Quick Reply 選擇回應) → parse → fuzzy match → 賣超防呆 → 寫入試算表 → 回覆
"""

import time
import uuid
from collections import defaultdict
from datetime import date as Date

from fastapi import APIRouter, Header, HTTPException, Request
from googleapiclient.errors import HttpError
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
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
from app.models.schemas import FriendRecord, FriendStatus, Position, StockQuote, TransactionRow
from app.routers.tick import get_cached_stock_list
from app.services.friend_repository import deactivate_friend, get_friend_record, reactivate_friend
from app.services.fuzzy_match import resolve_stock
from app.services.oauth_service import OAuthInvalidGrantError, build_authorization_url
from app.services.parser import parse_transaction_text
from app.services.pnl_engine import InsufficientPositionError, apply_transaction
from app.services.sheets_client import append_transaction_row, read_tab_positions

router = APIRouter()

# 短時間窗口去重(5~10 分鐘),而非永久記錄事件 ID — ADR-015。
_DEDUPE_WINDOW_SECONDS = 600
_recent_event_ids: dict[str, float] = {}

# 多帳戶 Quick Reply 選擇的暫存狀態(process 記憶體,5 分鐘有效)
_PENDING_SELECTION_WINDOW = 300
_pending_selections: dict[str, dict] = {}


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


# ── LINE 回覆工具 ─────────────────────────────────────────────────────────────

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
) -> tuple[list[str], list[str]]:
    """fuzzy match → 讀取庫存 → 賣超防呆 → 逐筆寫入試算表。
    回傳 (成功訊息列表, 錯誤訊息列表)。
    OAuth 失效或 Sheets API 錯誤直接往上拋,呼叫端負責回覆親友。
    """
    successes: list[str] = []
    errors: list[str] = []

    resolved: list[tuple] = []
    for txn in parsed_txns:
        try:
            stock = resolve_stock(txn.stock_query, stock_list)
            resolved.append((txn, stock))
        except ValueError:
            errors.append(f"找不到股票「{txn.stock_query}」")

    if not resolved:
        return successes, errors

    positions: dict[str, Position] = read_tab_positions(friend, tab_name, stock_list)

    for txn, stock in resolved:
        position = positions.get(stock.code, Position(stock_code=stock.code))
        try:
            new_position = apply_transaction(position, txn)
        except InsufficientPositionError:
            errors.append(f"「{stock.code} {stock.name}」庫存不足({position.quantity} 股),這筆略過")
            continue

        row = TransactionRow(
            row_uuid=str(uuid.uuid4()),
            date=Date.today(),
            action=txn.action,
            stock_query=f"{stock.code} {stock.name}",
            quantity=txn.quantity,
            amount=txn.amount,
        )
        append_transaction_row(friend, tab_name, row)
        positions[stock.code] = new_position

        label_parts = [txn.action.value, f"{stock.code} {stock.name}"]
        if txn.quantity is not None:
            label_parts.append(f"{txn.quantity} 股")
        if txn.amount is not None:
            label_parts.append(f"${txn.amount}")
        successes.append(" ".join(label_parts))

    return successes, errors


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
        successes, errors = _book_transactions(friend, tab_name, transactions, stock_list)
    except OAuthInvalidGrantError:
        url = build_authorization_url(friend.line_user_id)
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結才能繼續記帳:{url}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            url = build_authorization_url(friend.line_user_id)
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{url}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    if extra_errors:
        errors = extra_errors + errors
    _reply_text(reply_token, _format_booking_reply(successes, errors))


def _execute_booking_by_tag(
    reply_token: str,
    friend: FriendRecord,
    transactions: list,
    stock_list: list[StockQuote],
) -> None:
    """多帳戶且全部已標籤:依 account_tag 分組各自記帳,合併成一則回覆"""
    by_tab: dict[str, list] = defaultdict(list)
    for txn in transactions:
        by_tab[txn.account_tag].append(txn)

    all_successes: list[str] = []
    all_errors: list[str] = []
    try:
        for tab, txns in by_tab.items():
            s, e = _book_transactions(friend, tab, txns, stock_list)
            all_successes.extend(s)
            all_errors.extend(e)
    except OAuthInvalidGrantError:
        url = build_authorization_url(friend.line_user_id)
        _reply_text(reply_token, f"試算表授權已過期,需要重新連結才能繼續記帳:{url}")
        return
    except HttpError as exc:
        if exc.resp.status == 404:
            url = build_authorization_url(friend.line_user_id)
            _reply_text(reply_token, f"找不到試算表(可能已被刪除),需要重新連結:{url}")
        else:
            _reply_text(reply_token, "試算表連線異常,請稍後再試")
        return

    _reply_text(reply_token, _format_booking_reply(all_successes, all_errors))


# ── 事件處理 ──────────────────────────────────────────────────────────────────

def _handle_follow_event(event: FollowEvent) -> None:
    line_user_id = event.source.user_id
    friend = get_friend_record(line_user_id)
    if friend is not None and friend.status == FriendStatus.INACTIVE:
        reactivate_friend(line_user_id)


def _handle_unfollow_event(event: UnfollowEvent) -> None:
    line_user_id = event.source.user_id
    if get_friend_record(line_user_id) is not None:
        deactivate_friend(line_user_id)


def _handle_text_message(event: MessageEvent) -> None:
    line_user_id = event.source.user_id
    text = event.message.text.strip()

    friend = get_friend_record(line_user_id)
    if friend is None:
        url = build_authorization_url(line_user_id)
        _reply_text(event.reply_token, f"還沒有連結你自己的記帳試算表喔,點這裡授權一下:{url}")
        return

    if friend.status == FriendStatus.NEEDS_REAUTH:
        url = build_authorization_url(line_user_id)
        _reply_text(event.reply_token, f"試算表授權已過期,需要重新連結才能繼續記帳:{url}")
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
        _reply_text(event.reply_token, "解析失敗:\n" + "\n".join(error_lines))
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
        _execute_booking_by_tag(event.reply_token, friend, parse_result.transactions, stock_list)
    else:
        _set_pending(line_user_id, parse_result.transactions, tabs)
        _reply_with_quick_reply(event.reply_token, "請問要記在哪個帳戶?", tabs[:13])


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
