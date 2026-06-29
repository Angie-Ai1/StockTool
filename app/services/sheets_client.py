"""親友試算表建立與 resync — 規格 2.2、3.1、1.6。

`copy_template_to_drive()` 服務 1.3 OAuth 連結流程(複製範本進親友 Drive)。
`resync()` 服務 1.6:重新讀取親友試算表所有分頁 → 依欄位標題列結構辨認帳戶分頁 →
模糊比對解析股票 → 用 pnl_engine 重算損益 → 把驗證結果寫回每列的「狀態」欄 →
全部分頁都寫回成功後,才整批覆寫 Firestore `account_tabs_cache`(不是局部增修)。

計算出來的庫存/損益(`ResyncResult`)不額外持久化——這是 cache-aside 設計
(技術文件第 1 章):資料隨時可以從試算表這個 source of truth 重建,呼叫端
(LINE 查詢/LIFF/排程任務)用完即丟,下次需要再呼叫 `resync()` 重新算一次即可。
"""

from collections import defaultdict
from datetime import date as Date, timedelta
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

MAX_SUMMARY_STOCKS = 30

# 統計摘要版面（Sheets 1-indexed 列號）：合計列在最上面 row1、標頭兩列在 row2-3、
# 個股資料從 row4 起。標頭兩列（含合計列）凍結，捲動個股時保持可見。
SUMMARY_TOTAL_ROW = 1
SUMMARY_TITLE_ROW = 2
SUMMARY_HEADER_ROW = 3
SUMMARY_FIRST_STOCK_ROW = 4
SUMMARY_LAST_STOCK_ROW = SUMMARY_FIRST_STOCK_ROW + MAX_SUMMARY_STOCKS - 1  # 33
SUMMARY_FROZEN_ROWS = 3  # 凍結 row1 合計 + row2-3 標頭（流水帳前兩列也會一併凍結，為共用版面限制）


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
    # Sheets 以 USER_ENTERED 寫入日期字串時，若儲存格無 DATE 格式，
    # FORMATTED_VALUE 會回傳 serial number（如 46201 代表 2026-06-28）
    if raw.isdigit():
        return Date(1899, 12, 30) + timedelta(days=int(raw))
    # 台灣語系預設格式為 yyyy/M/d（不補零），fromisoformat 不接受無補零格式，
    # 改用 Date(y, m, d) constructor 直接解析任意分隔與無補零日期。
    parts = raw.replace("/", "-").split("-")
    if len(parts) == 3:
        try:
            return Date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, OverflowError):
            pass
    raise ValueError(f"無法辨識的日期「{raw}」")


def _parse_decimal(raw: str, field_name: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{field_name}格式錯誤「{raw}」") from exc


_ACTION_COMPAT = {"買": TransactionAction.BUY, "賣": TransactionAction.SELL}


def _parse_sheet_row(row: list[str], header_index: dict[str, int]) -> TransactionRow:
    action_raw = _cell(row, header_index, "動作")
    try:
        action = TransactionAction(action_raw)
    except ValueError as exc:
        if action_raw in _ACTION_COMPAT:
            action = _ACTION_COMPAT[action_raw]
        else:
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


def _write_summary_formulas(service, spreadsheet_id: str, tab_title: str) -> None:
    """重寫統計摘要公式區（I1:Q33）。
    每次 resync 都呼叫，確保即使之前因 INSERT_ROWS 造成偏移，也能恢復到正確位置。

    版面：合計列在 row1、標頭兩列在 row2-3、個股從 row4 起（見 SUMMARY_*_ROW 常數）。
    今日收盤價（N 欄）用 VLOOKUP 引用後端每次 resync 寫入的隱藏報價參考區 S:T
    （見 `_write_price_reference`）；買進平均價/未實現/已實現損益則由 N、K、L 等
    欄位即時運算，使用者編輯流水帳時會跟著重算（收盤價要等下次 resync 才更新）。
    """
    first = SUMMARY_FIRST_STOCK_ROW       # 個股第一列（row 4）
    last = SUMMARY_LAST_STOCK_ROW         # 個股最後一列（row 33）
    price_end_row = 1 + MAX_SUMMARY_STOCKS  # 報價參考區 S2:T31 的最後一列

    # row1：合計（彙總 row4~row33 的個股）
    summary: list[list[str]] = [
        [
            "合計", "",
            f"=SUM(K{first}:K{last})",
            f"=SUM(L{first}:L{last})",
            f"=SUM(M{first}:M{last})",
            "",  # 今日收盤價無合計
            "",  # 買進平均價無合計
            f"=SUM(P{first}:P{last})",
            f"=SUM(Q{first}:Q{last})",
        ],
        # row2：標題
        ["📊 統計摘要", "", "", "", "", "", "", "", ""],
        # row3：欄位標頭
        ["個股", "持股數", "買入金額", "賣出金額", "配息收入", "今日收盤價", "買進平均價", "未實現損益", "已實現損益"],
    ]
    for n in range(1, MAX_SUMMARY_STOCKS + 1):
        sr = SUMMARY_HEADER_ROW + n  # 個股列：row 4..33
        ir = f"I{sr}"
        buy_qty = f'SUMIFS($E$2:$E$2000,$C$2:$C$2000,"買進",$D$2:$D$2000,{ir})'
        sell_qty = f'SUMIFS($E$2:$E$2000,$C$2:$C$2000,"賣出",$D$2:$D$2000,{ir})'
        summary.append([
            f'=IFERROR(INDEX(SORT(UNIQUE(FILTER($D$2:$D$2000,($C$2:$C$2000<>"")*($D$2:$D$2000<>"")))),{n},1),"")',
            (
                f'=IF({ir}="","",SUMIFS($E$2:$E$2000,$C$2:$C$2000,"買進",$D$2:$D$2000,{ir})'
                f'-SUMIFS($E$2:$E$2000,$C$2:$C$2000,"賣出",$D$2:$D$2000,{ir})'
                f'+SUMIFS($E$2:$E$2000,$C$2:$C$2000,"配股",$D$2:$D$2000,{ir}))'
            ),
            f'=IF({ir}="","",SUMIFS($F$2:$F$2000,$C$2:$C$2000,"買進",$D$2:$D$2000,{ir}))',
            f'=IF({ir}="","",SUMIFS($F$2:$F$2000,$C$2:$C$2000,"賣出",$D$2:$D$2000,{ir}))',
            f'=IF({ir}="","",SUMIFS($F$2:$F$2000,$C$2:$C$2000,"配息",$D$2:$D$2000,{ir}))',
            # 今日收盤價 N：查後端寫入的報價參考區（找不到留空）
            f'=IF({ir}="","",IFERROR(VLOOKUP({ir},$S$2:$T${price_end_row},2,FALSE),""))',
            # 買進平均價 O = 買入金額 / 買進股數
            f'=IF({ir}="","",IFERROR(K{sr}/{buy_qty},""))',
            # 未實現損益 P =（今日收盤價 - 買進平均價）× 持股數
            f'=IF(OR({ir}="",N{sr}="",O{sr}=""),"",(N{sr}-O{sr})*J{sr})',
            # 已實現損益 Q = 賣出金額 - 賣出股數 × 買進平均價
            f'=IF(OR({ir}="",O{sr}=""),"",L{sr}-{sell_qty}*O{sr})',
        ])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_title}'!I1",
        valueInputOption="USER_ENTERED",
        body={"values": summary},
    ).execute()


def _write_price_reference(
    service,
    spreadsheet_id: str,
    tab_title: str,
    data_rows: list[list[str]],
    header_index: dict[str, int],
    stock_list: list[StockQuote],
) -> None:
    """把每檔股票今日收盤價寫進隱藏報價參考區 S2:T31，供統計摘要 N 欄 VLOOKUP 引用。

    key 用流水帳「股票代碼/名稱」的原始文字，與統計摘要 I 欄
    （`UNIQUE(FILTER(D...))`）取的值一致，VLOOKUP 才比對得到。固定寫滿
    MAX_SUMMARY_STOCKS 列（不足補空白），順便清掉上一次殘留的舊報價。
    """
    seen: dict[str, str] = {}
    for row in data_rows:
        query = _cell(row, header_index, "股票代碼/名稱")
        if not query or query in seen:
            continue
        try:
            stock = resolve_stock(query, stock_list)
        except ValueError:
            continue
        if stock.close is not None:
            seen[query] = str(stock.close)
        if len(seen) >= MAX_SUMMARY_STOCKS:
            break

    values: list[list[str]] = [[query, close] for query, close in seen.items()]
    while len(values) < MAX_SUMMARY_STOCKS:
        values.append(["", ""])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_title}'!S2:T{1 + MAX_SUMMARY_STOCKS}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def _hex(h: str) -> dict:
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def _format_summary_columns(service, spreadsheet_id: str, sheet_id: int) -> None:
    """統計摘要區（I1:Q33）的視覺格式 —— 冪等，每次 resync 都套用。

    版面：合計列 row1、標題 row2、欄位標頭 row3、個股 row4~row33。先 unmerge 涵蓋
    row1-3 的範圍再 merge 新標題列 I2:Q2，讓舊版（標題在 row1、I:M 五欄）能無痛升級；
    並凍結前三列（合計+標頭兩列）、隱藏報價參考區 S:T。所有請求皆冪等，重複套用不會疊加。
    """
    def _cells(r0: int, r1: int, c0: int, c1: int) -> dict:
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def _border(color: str = "BDBDBD") -> dict:
        return {"style": "SOLID", "width": 1, "color": _hex(color)}

    def _col(s: int, e: int) -> dict:
        return {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": s, "endIndex": e}

    SCOL = 8   # Column I
    SEND = 17  # Column R exclusive（I~Q，九欄）
    # 0-indexed 列：合計 row1→0、標題 row2→1、標頭 row3→2、個股 row4~33→3~32、區塊底→33
    TOTAL_I = SUMMARY_TOTAL_ROW - 1        # 0
    TITLE_I = SUMMARY_TITLE_ROW - 1        # 1
    HEADER_I = SUMMARY_HEADER_ROW - 1      # 2
    STOCK_I0 = SUMMARY_FIRST_STOCK_ROW - 1  # 3
    BLOCK_END = SUMMARY_LAST_STOCK_ROW      # 33（exclusive 結尾，剛好等於最後一列列號）
    PNL_FMT = {"type": "NUMBER", "pattern": "#,##0;[Red]-#,##0"}  # 損益欄：負值標紅

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            # 凍結前三列（合計 + 標頭兩列），捲動個股時保持可見
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": SUMMARY_FROZEN_ROWS}},
                "fields": "gridProperties.frozenRowCount",
            }},
            # 標題列 I2:Q2 合併（先 unmerge row1-3 範圍以相容舊版面）+ 樣式
            {"unmergeCells": {"range": _cells(TOTAL_I, HEADER_I + 1, SCOL, SEND)}},
            {"mergeCells": {"range": _cells(TITLE_I, TITLE_I + 1, SCOL, SEND), "mergeType": "MERGE_ALL"}},
            {"repeatCell": {
                "range": _cells(TITLE_I, TITLE_I + 1, SCOL, SEND),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": _hex("EFEFEF"),
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat.bold,horizontalAlignment)",
            }},
            # 欄位標頭列 I3:Q3
            {"repeatCell": {
                "range": _cells(HEADER_I, HEADER_I + 1, SCOL, SEND),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": _hex("F5F5F5"),
                    "textFormat": {"bold": True},
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
            }},
            # 合計列（row1）加粗
            {"repeatCell": {
                "range": _cells(TOTAL_I, TOTAL_I + 1, SCOL, SEND),
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }},
            # 數字格式：金額欄 K-M（合計列 + 個股列；標題/標頭為文字不受影響）
            {"repeatCell": {
                "range": _cells(TOTAL_I, BLOCK_END, 10, 13),
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                "fields": "userEnteredFormat.numberFormat",
            }},
            # 數字格式：損益欄 P-Q（合計列 + 個股列，負值標紅）
            {"repeatCell": {
                "range": _cells(TOTAL_I, BLOCK_END, 15, 17),
                "cell": {"userEnteredFormat": {"numberFormat": PNL_FMT}},
                "fields": "userEnteredFormat.numberFormat",
            }},
            # 數字格式：收盤價 N / 買進平均價 O（個股列，含小數）
            {"repeatCell": {
                "range": _cells(STOCK_I0, BLOCK_END, 13, 15),
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.##"}}},
                "fields": "userEnteredFormat.numberFormat",
            }},
            # 數字格式：持股欄 J（個股列）
            {"repeatCell": {
                "range": _cells(STOCK_I0, BLOCK_END, 9, 10),
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.##"}}},
                "fields": "userEnteredFormat.numberFormat",
            }},
            # 統計摘要框線 I1:Q33（內框線比照流水帳用 BDBDBD，避免太淡看不見）
            {"updateBorders": {
                "range": _cells(TOTAL_I, BLOCK_END, SCOL, SEND),
                "top": _border(), "bottom": _border(),
                "left": _border(), "right": _border(),
                "innerHorizontal": _border(), "innerVertical": _border(),
            }},
            # 欄寬：I 個股 / J-Q 數字欄
            {"updateDimensionProperties": {"range": _col(8, 9), "properties": {"pixelSize": 130}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": _col(9, 17), "properties": {"pixelSize": 90}, "fields": "pixelSize"}},
            # 隱藏報價參考區 S:T（cols 18-19）
            {"updateDimensionProperties": {"range": _col(18, 20), "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        ]},
    ).execute()


def _apply_tab_format(service, spreadsheet_id: str, sheet_id: int, tab_title: str) -> None:
    """流水帳一次性格式（表格線、條件格式）——新分頁建立時呼叫。

    統計摘要的公式由 `_write_summary_formulas`、視覺格式由 `_format_summary_columns`
    各自負責，且必須「先解除合併、再寫公式」（見 resync 內呼叫順序），否則 row1 合計列
    的 SUM 公式會被殘留的標題合併儲存格吞掉。這裡只負責流水帳區那些「重複套用會疊加」的
    一次性設定（條件格式規則）。
    """
    def _cells(r0: int, r1: int, c0: int, c1: int) -> dict:
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def _border(color: str = "BDBDBD") -> dict:
        return {"style": "SOLID", "width": 1, "color": _hex(color)}

    def _col(s: int, e: int) -> dict:
        return {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": s, "endIndex": e}

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            # 流水帳表格線 B1:G1000
            {"updateBorders": {
                "range": _cells(0, 1000, 1, 7),
                "top": _border(), "bottom": _border(),
                "left": _border(), "right": _border(),
                "innerHorizontal": _border(), "innerVertical": _border(),
            }},
            # H 空欄當作流水帳與統計摘要之間的間隔
            {"updateDimensionProperties": {"range": _col(7, 8), "properties": {"pixelSize": 20}, "fields": "pixelSize"}},
            # 條件格式：買進列 → 淡綠
            {"addConditionalFormatRule": {
                "rule": {
                    "ranges": [_cells(1, 1000, 1, 7)],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": '=$C2="買進"'}]},
                        "format": {"backgroundColor": _hex("D9EFD9")},
                    },
                },
                "index": 0,
            }},
            # 條件格式：賣出列 → 淡紅
            {"addConditionalFormatRule": {
                "rule": {
                    "ranges": [_cells(1, 1000, 1, 7)],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": '=$C2="賣出"'}]},
                        "format": {"backgroundColor": _hex("FDDCDC")},
                    },
                },
                "index": 1,
            }},
        ]},
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
        sheet_id = sheet["properties"]["sheetId"]
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

        # 首次 resync 時（含舊分頁升級）套用流水帳一次性視覺格式（內含公式寫入）；
        # 已有摘要的分頁只重寫公式區，修正任何因舊版 INSERT_ROWS 造成的偏移。
        # 偵測 I 欄前三列是否出現摘要標記：相容舊版（標題在 row1）與新版（合計在
        # row1、標題在 row2），只要看得到任一標記就視為已建過摘要，不再跑一次性格式。
        col_i_top = [r[8] if len(r) > 8 else "" for r in rows[:3]]
        has_summary = "📊 統計摘要" in col_i_top or "合計" in col_i_top
        if not has_summary:
            _apply_tab_format(service, friend.spreadsheet_id, sheet_id, title)

        # 順序很重要：先套統計摘要格式（內含解除舊標題合併），再寫公式。
        # 否則殘留的標題合併會吞掉 row1 合計列的 SUM 公式（只剩左上角「合計」）。
        # 報價參考區每次都刷新，讓今日收盤價更新；舊版 I:M 分頁也在此自動升級。
        _format_summary_columns(service, friend.spreadsheet_id, sheet_id)
        _write_summary_formulas(service, friend.spreadsheet_id, title)
        _write_price_reference(service, friend.spreadsheet_id, title, rows[1:], header_index, stock_list)

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

    # 只寫 A–G 欄（REQUIRED_HEADERS 範圍），I–M 的統計摘要不受影響
    num_cols = len(REQUIRED_HEADERS)
    new_row = [""] * num_cols
    field_values = {
        "row_uuid": txn.row_uuid,
        "日期": f"=DATE({txn.date.year},{txn.date.month},{txn.date.day})",
        "動作": txn.action.value,
        "股票代碼/名稱": txn.stock_query,
        "數量": str(txn.quantity) if txn.quantity is not None else "",
        "金額": str(txn.amount) if txn.amount is not None else "",
        "狀態": txn.status,
    }
    for col_name, col_idx in header_index.items():
        if col_name in field_values and col_idx < num_cols:
            new_row[col_idx] = field_values[col_name]

    # 讀取 A 欄（row_uuid）計算下一個空列。
    # 不用 append+OVERWRITE：OVERWRITE 的表格邊界偵測會把 I–M 欄的統計公式算進去，
    # 導致每次都寫到同一列（永遠覆蓋第一筆）。用 update 直接指定列號最可靠。
    uuid_col = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=friend.spreadsheet_id, range=f"'{tab_name}'!A:A")
        .execute()
    )
    next_row = max(len(uuid_col.get("values", [])) + 1, 2)

    service.spreadsheets().values().update(
        spreadsheetId=friend.spreadsheet_id,
        range=f"'{tab_name}'!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [new_row]},
    ).execute()


def delete_transaction_rows(
    friend: FriendRecord,
    written_rows: list[tuple[str, str]],
    *,
    credentials_builder=build_credentials_from_encrypted_refresh_token,
    refresher=refresh_or_raise,
    sheets_service_builder=lambda credentials: build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ),
    firestore_client=None,
) -> int:
    """依 row_uuid 批次刪除試算表中的列，回傳實際刪除數——規格 1.7"""
    if not written_rows:
        return 0

    credentials = credentials_builder(friend.encrypted_refresh_token)
    try:
        refresher(credentials)
    except OAuthInvalidGrantError:
        mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    service = sheets_service_builder(credentials)

    try:
        spreadsheet_meta = service.spreadsheets().get(
            spreadsheetId=friend.spreadsheet_id
        ).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    sheet_id_by_title: dict[str, int] = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in spreadsheet_meta.get("sheets", [])
    }

    by_tab: dict[str, list[str]] = defaultdict(list)
    for tab_name, row_uuid in written_rows:
        by_tab[tab_name].append(row_uuid)

    delete_requests: list[dict] = []
    total_deleted = 0

    for tab_name, uuids in by_tab.items():
        sheet_id = sheet_id_by_title.get(tab_name)
        if sheet_id is None:
            continue

        try:
            response = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=friend.spreadsheet_id, range=f"'{tab_name}'")
                .execute()
            )
        except HttpError:
            continue

        rows = response.get("values", [])
        if not rows:
            continue

        header_index = map_header_columns(rows[0])
        if header_index is None:
            continue

        uuid_col = header_index.get("row_uuid")
        if uuid_col is None:
            continue

        uuid_set = set(uuids)
        row_indices: list[int] = []
        for i, row in enumerate(rows):
            if i == 0:
                continue
            cell = row[uuid_col] if uuid_col < len(row) else ""
            if str(cell).strip() in uuid_set:
                row_indices.append(i)

        # 由下往上刪，避免先刪上方列後後續索引偏移
        for row_index in sorted(row_indices, reverse=True):
            delete_requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_index,
                        "endIndex": row_index + 1,
                    }
                }
            })
            total_deleted += 1

    if delete_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=friend.spreadsheet_id,
            body={"requests": delete_requests},
        ).execute()

    return total_deleted


def create_account_tab(
    friend: FriendRecord,
    tab_name: str,
    *,
    credentials_builder=build_credentials_from_encrypted_refresh_token,
    refresher=refresh_or_raise,
    sheets_service_builder=lambda credentials: build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ),
    firestore_client=None,
) -> None:
    """在親友試算表新增帳戶分頁並套用標準格式(凍結標題、資料驗證、欄寬、隱藏 UUID 欄)。

    新分頁自動寫入 REQUIRED_HEADERS 並更新 Firestore account_tabs_cache。
    分頁名稱重複或含非法字元時 Sheets API 會回 HttpError(400),由呼叫端處理。
    """
    credentials = credentials_builder(friend.encrypted_refresh_token)
    try:
        refresher(credentials)
    except OAuthInvalidGrantError:
        mark_needs_reauth(friend.line_user_id, firestore_client=firestore_client)
        raise

    service = sheets_service_builder(credentials)

    # 新增分頁，取得 sheetId
    response = service.spreadsheets().batchUpdate(
        spreadsheetId=friend.spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    # 寫入標題列
    service.spreadsheets().values().update(
        spreadsheetId=friend.spreadsheet_id,
        range=f"'{tab_name}'!A1:G1",
        valueInputOption="RAW",
        body={"values": [list(REQUIRED_HEADERS)]},
    ).execute()

    # 套用格式：凍結、標題樣式、資料驗證、狀態欄底色、隱藏 UUID 欄、欄寬
    def _hex(h: str) -> dict:
        h = h.lstrip("#")
        return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}

    def _col(start: int, end: int) -> dict:
        return {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start, "endIndex": end}

    def _cells(r0: int, r1: int, c0: int, c1: int) -> dict:
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    format_requests: list[dict] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": SUMMARY_FROZEN_ROWS}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"repeatCell": {
            "range": _cells(0, 1, 0, 7),
            "cell": {"userEnteredFormat": {
                "backgroundColor": _hex("EFEFEF"),
                "textFormat": {"bold": True},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }},
        {"repeatCell": {
            "range": _cells(1, 1000, 1, 2),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy/mm/dd"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        {"setDataValidation": {
            "range": _cells(1, 1000, 2, 3),
            "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [
                    {"userEnteredValue": "買進"},
                    {"userEnteredValue": "賣出"},
                    {"userEnteredValue": "配息"},
                    {"userEnteredValue": "配股"},
                ]},
                "showCustomUi": True,
                "strict": True,
            },
        }},
        {"repeatCell": {
            "range": _cells(1, 1000, 6, 7),
            "cell": {"userEnteredFormat": {"backgroundColor": _hex("F5F5F5")}},
            "fields": "userEnteredFormat.backgroundColor",
        }},
        {"updateDimensionProperties": {
            "range": _col(0, 1),
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }},
    ]
    for col, px in [(1, 90), (2, 70), (3, 150), (4, 90), (5, 90)]:
        format_requests.append({"updateDimensionProperties": {
            "range": _col(col, col + 1),
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=friend.spreadsheet_id,
        body={"requests": format_requests},
    ).execute()

    _apply_tab_format(service, friend.spreadsheet_id, sheet_id, tab_name)
    # 先格式（含解除合併）再寫公式，避免標題合併吞掉合計列 SUM 公式
    _format_summary_columns(service, friend.spreadsheet_id, sheet_id)
    _write_summary_formulas(service, friend.spreadsheet_id, tab_name)

    # Firestore cache：把新分頁名稱加入已辨識的帳戶清單
    existing = list(friend.account_tabs_cache or [])
    if tab_name not in existing:
        update_account_tabs_cache(
            friend.line_user_id,
            existing + [tab_name],
            firestore_client=firestore_client,
        )
