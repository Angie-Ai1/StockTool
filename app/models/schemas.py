from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class FriendStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    NEEDS_REAUTH = "needs_reauth"


class FriendRecord(BaseModel):
    """Firestore friends/{line_user_id} — technical_spec.md 3.1"""

    line_user_id: str
    spreadsheet_id: str
    encrypted_refresh_token: str
    account_tabs_cache: list[str] = []
    status: FriendStatus = FriendStatus.ACTIVE
    last_daily_job_synced_at: datetime | None = None


class SchedulerState(BaseModel):
    """Firestore system/scheduler — technical_spec.md 3.1"""

    last_run_date: str | None = None


class TransactionAction(str, Enum):
    BUY = "買"
    SELL = "賣"
    DIVIDEND = "股息"
    STOCK_DIVIDEND = "配股"


class TransactionRow(BaseModel):
    """Google Sheets 每筆交易紀錄 — technical_spec.md 3.2"""

    row_uuid: str
    date: Date
    action: TransactionAction
    stock_query: str
    quantity: Decimal | None = None
    amount: Decimal | None = None
    status: str = ""


class ParsedTransaction(BaseModel):
    """parser.parse_transaction_text() 對單一行的解析結果,尚未做股票模糊比對"""

    raw_text: str
    action: TransactionAction
    stock_query: str
    quantity: Decimal | None = None
    amount: Decimal | None = None
    account_tag: str | None = None
    unit_price: Decimal | None = None


class ParseError(BaseModel):
    line_number: int
    raw_text: str
    reason: str


class ParseResult(BaseModel):
    transactions: list[ParsedTransaction] = []
    errors: list[ParseError] = []


class StockQuote(BaseModel):
    """單一證券的代碼/名稱/收盤價 — 規格 6.1,TWSE(上市)/TPEx(上櫃)清單共用欄位"""

    code: str
    name: str
    close: Decimal | None = None


class Position(BaseModel):
    """單一帳戶內、單一股票的目前庫存與均價 — 規格 5.2"""

    stock_code: str
    quantity: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


class AccountResyncResult(BaseModel):
    """單一帳戶(分頁)resync 後的庫存結果 — 規格 1.6"""

    tab_name: str
    positions: list[Position] = []


class ResyncResult(BaseModel):
    """sheets_client.resync() 的回傳值,供 LINE 查詢/LIFF/排程任務直接使用,不另外持久化 — 規格 1.6"""

    accounts: list[AccountResyncResult] = []


class PositionSummary(BaseModel):
    """LIFF 網頁顯示用的單一持股摘要,補上 resync 沒有的股票名稱/目前收盤價/未實現損益 — 規格 1.11、7.2"""

    stock_code: str
    stock_name: str
    quantity: Decimal
    avg_cost: Decimal
    realized_pnl: Decimal
    closing_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None


class AccountSummary(BaseModel):
    """LIFF 網頁顯示用的單一帳戶(分頁)摘要 — 規格 1.11"""

    tab_name: str
    positions: list[PositionSummary] = []


class LiffSummaryResponse(BaseModel):
    """`GET /liff/summary` 回應 — 規格 1.11:登入連結狀態、目前庫存列表、簡單損益顯示"""

    linked: bool
    status: FriendStatus | None = None
    accounts: list[AccountSummary] = []
