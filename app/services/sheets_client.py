"""親友試算表建立與 resync — 規格 2.2、3.1、1.6。

`copy_template_to_drive()` 服務 1.3 OAuth 連結流程(複製範本進親友 Drive)。
`resync()` 服務 1.6:重新讀取親友試算表所有分頁 → 依欄位標題列結構辨認帳戶分頁 →
模糊比對解析股票 → 用 pnl_engine 重算損益 → 把驗證結果寫回每列的「狀態」欄 →
全部分頁都寫回成功後,才整批覆寫 Firestore `account_tabs_cache`(不是局部增修)。

計算出來的庫存/損益(`ResyncResult`)不額外持久化——這是 cache-aside 設計
(技術文件第 1 章):資料隨時可以從試算表這個 source of truth 重建,呼叫端
(LINE 查詢/LIFF/排程任務)用完即丟,下次需要再呼叫 `resync()` 重新算一次即可。
"""

from datetime import date as Date
from decimal import Decimal, InvalidOperation

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.models.schemas import (
    AccountResyncResult,
    FriendRecord,
    ParsedTransaction,
    Position,
    ResyncResult,
    StockQuote,
    TransactionAction,
    TransactionRow,
)
from app.services.friend_repository import mark_needs_reauth, update_account_tabs_cache
from app.services.fuzzy_match import resolve_stock
from app.services.oauth_service import (
    OAuthInvalidGrantError,
    build_credentials_from_encrypted_refresh_token,
    refresh_or_raise,
)
from app.services.pnl_engine import InsufficientPositionError, apply_transaction

DEFAULT_SPREADSHEET_NAME = "股市記帳"

# 帳戶分頁的標題列必須同時具備這些欄位才會被辨認為「帳戶分頁」——規格 1.6、
# 技術文件 3.2。親友若把標題列改掉,後端會認不出來(規格已知限制,寫進範本提示文字)。
REQUIRED_HEADERS = ("row_uuid", "日期", "動作", "股票代碼/名稱", "數量", "金額", "狀態")

STATUS_OK = ""
STATUS_INVALID_ROW = "⚠️ 無法辨識,請修正"
STATUS_OVERSOLD = "⚠️ 賣出超過庫存,請修正"


def copy_template_to_drive(
    credentials: Credentials, template_id: str, file_name: str = DEFAULT_SPREADSHEET_NAME
) -> str:
    """用親友自己的授權,把範本複製到他自己的 Drive,回傳新檔案的 spreadsheet_id——規格 2.2 步驟 3"""
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    copied = drive_service.files().copy(fileId=template_id, body={"name": file_name}).execute()
    return copied["id"]


def map_header_columns(header_row: list[str]) -> dict[str, int] | None:
    """標題列符合規格才回傳欄位名→欄位索引的對照表,否則回傳 None(代表不是帳戶分頁)"""
    index = {name: i for i, name in enumerate(header_row)}
    if not all(header in index for header in REQUIRED_HEADERS):
        return None
    return index


def _cell(row: list[str], header_index: dict[str, int], header: str) -> str:
    column = header_index[header]
    if column >= len(row) or row[column] is None:
        return ""
    return str(row[column]).strip()


def _parse_date(raw: str) -> Date:
    for candidate in (raw, raw.replace("/", "-")):
        try:
            return Date.fromisoformat(candidate)
        except ValueError:
            continue
    raise ValueError(f"無法辨識的日期「{raw}」")


def _parse_decimal(raw: str, field_name: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{field_name}格式錯誤「{raw}」") from exc


def _parse_sheet_row(row: list[str], header_index: dict[str, int]) -> TransactionRow:
    action_raw = _cell(row, header_index, "動作")
    try:
        action = TransactionAction(action_raw)
    except ValueError as exc:
        raise ValueError(f"無法辨識的動作「{action_raw}」") from exc

    quantity_raw = _cell(row, header_index, "數量")
    amount_raw = _cell(row, header_index, "金額")

    return TransactionRow(
        row_uuid=_cell(row, header_index, "row_uuid"),
        date=_parse_date(_cell(row, header_index, "日期")),
        action=action,
        stock_query=_cell(row, header_index, "股票代碼/名稱"),
        quantity=_parse_decimal(quantity_raw, "數量") if quantity_raw else None,
        amount=_parse_decimal(amount_raw, "金額") if amount_raw else None,
    )


def resync_account_tab(
    rows: list[list[str]], header_index: dict[str, int], stock_list: list[StockQuote]
) -> tuple[dict[str, Position], list[str]]:
    """依序套用一個帳戶分頁裡的每一列交易——規格 1.6 核心邏輯,純函式不打任何外部 API。

    無法辨識的股票/賣超的列不計入損益、不污染後面列的計算,只在回傳的狀態列表中標記;
    呼叫端負責把這份狀態列表寫回 Sheet 的「狀態」欄。
    """
    positions: dict[str, Position] = {}
    statuses: list[str] = []

    for row in rows:
        if not _cell(row, header_index, "動作"):
            statuses.append(STATUS_OK)
            continue

        try:
            txn_row = _parse_sheet_row(row, header_index)
            stock = resolve_stock(txn_row.stock_query, stock_list)
        except ValueError:
            statuses.append(STATUS_INVALID_ROW)
            continue

        position = positions.get(stock.code, Position(stock_code=stock.code))
        parsed_txn = ParsedTransaction(
            raw_text="",
            action=txn_row.action,
            stock_query=txn_row.stock_query,
            quantity=txn_row.quantity,
            amount=txn_row.amount,
        )
        try:
            positions[stock.code] = apply_transaction(position, parsed_txn)
        except InsufficientPositionError:
            statuses.append(STATUS_OVERSOLD)
            continue
        except ValueError:
            statuses.append(STATUS_INVALID_ROW)
            continue

        statuses.append(STATUS_OK)

    return positions, statuses


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_status_column(
    service, spreadsheet_id: str, tab_title: str, header_index: dict[str, int], statuses: list[str]
) -> None:
    if not statuses:
        return
    column = _column_letter(header_index["狀態"])
    value_range = f"'{tab_title}'!{column}2:{column}{len(statuses) + 1}"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=value_range,
        valueInputOption="RAW",
        body={"values": [[status] for status in statuses]},
    ).execute()


def resync(
    friend: FriendRecord,
    stock_list: list[StockQuote],
    *,
    credentials_builder=build_credentials_from_encrypted_refresh_token,
    refresher=refresh_or_raise,
    sheets_service_builder=lambda credentials: build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ),
    firestore_client=None,
) -> ResyncResult:
    """重新讀取親友試算表所有分頁,重算損益並寫回狀態欄——規格 1.6。

    OAuth 失效(`invalid_grant`)或親友刪除整份試算表(Sheets API 404)都標記
    Firestore 狀態為需要重新連結,共用 1.3 已做好的 `mark_needs_reauth()`,並把例外
    往上拋,讓呼叫端(之後的 1.9 排程迴圈)決定要不要繼續處理下一位親友。
    """
    credentials = credentials_builder(friend.encrypted_refresh_token)
    try:
        refresher(credentials)
    except OAuthInvalidGrantError:
        mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    service = sheets_service_builder(credentials)

    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=friend.spreadsheet_id).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    accounts: list[AccountResyncResult] = []
    registered_tabs: list[str] = []

    for sheet in spreadsheet.get("sheets", []):
        title = sheet["properties"]["title"]
        values_response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=friend.spreadsheet_id, range=f"'{title}'")
            .execute()
        )
        rows = values_response.get("values", [])
        if not rows:
            continue

        header_index = map_header_columns(rows[0])
        if header_index is None:
            continue  # 標題列結構不符,不是帳戶分頁——規格 1.6

        positions, statuses = resync_account_tab(rows[1:], header_index, stock_list)
        _write_status_column(service, friend.spreadsheet_id, title, header_index, statuses)

        registered_tabs.append(title)
        accounts.append(AccountResyncResult(tab_name=title, positions=list(positions.values())))

    update_account_tabs_cache(friend.line_user_id, registered_tabs, firestore_client=firestore_client)

    return ResyncResult(accounts=accounts)


def read_tab_positions(
    friend: FriendRecord,
    tab_name: str,
    stock_list: list[StockQuote],
    *,
    credentials_builder=build_credentials_from_encrypted_refresh_token,
    refresher=refresh_or_raise,
    sheets_service_builder=lambda credentials: build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ),
    firestore_client=None,
) -> dict[str, Position]:
    """讀取一個帳戶分頁目前的庫存(純讀取,不寫回試算表)——供記帳前的賣超防呆用"""
    credentials = credentials_builder(friend.encrypted_refresh_token)
    try:
        refresher(credentials)
    except OAuthInvalidGrantError:
        mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    service = sheets_service_builder(credentials)
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=friend.spreadsheet_id, range=f"'{tab_name}'")
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    rows = response.get("values", [])
    if not rows:
        return {}
    header_index = map_header_columns(rows[0])
    if header_index is None:
        return {}
    positions, _ = resync_account_tab(rows[1:], header_index, stock_list)
    return positions


def append_transaction_row(
    friend: FriendRecord,
    tab_name: str,
    txn: TransactionRow,
    *,
    credentials_builder=build_credentials_from_encrypted_refresh_token,
    refresher=refresh_or_raise,
    sheets_service_builder=lambda credentials: build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ),
    firestore_client=None,
) -> None:
    """把一筆新交易追加到指定帳戶分頁的下一列——規格 1.2 記帳寫入流程"""
    credentials = credentials_builder(friend.encrypted_refresh_token)
    try:
        refresher(credentials)
    except OAuthInvalidGrantError:
        mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    service = sheets_service_builder(credentials)
    try:
        header_response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=friend.spreadsheet_id, range=f"'{tab_name}'!1:1")
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    header_rows = header_response.get("values", [])
    if not header_rows:
        raise ValueError(f"找不到帳戶分頁「{tab_name}」")
    header_index = map_header_columns(header_rows[0])
    if header_index is None:
        raise ValueError(f"「{tab_name}」標題列結構不符規格")

    num_cols = len(header_rows[0])
    new_row = [""] * num_cols
    field_values = {
        "row_uuid": txn.row_uuid,
        "日期": str(txn.date),
        "動作": txn.action.value,
        "股票代碼/名稱": txn.stock_query,
        "數量": str(txn.quantity) if txn.quantity is not None else "",
        "金額": str(txn.amount) if txn.amount is not None else "",
        "狀態": txn.status,
    }
    for col_name, col_idx in header_index.items():
        if col_name in field_values and col_idx < num_cols:
            new_row[col_idx] = field_values[col_name]

    service.spreadsheets().values().append(
        spreadsheetId=friend.spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [new_row]},
    ).execute()
