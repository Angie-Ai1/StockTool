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
    BUY = "買進"
    SELL = "賣出"
    DIVIDEND = "配息"
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


# --- 動態圖表網頁：時間序歷史（階段 1 重放流水帳重建；market_value 等需階段 3 快照才有值）---


class HistoryPoint(BaseModel):
    """某一天的累積快照。

    `cost_basis`(持倉成本=Σ均價×股數)與 `realized_pnl`(累積已實現損益)純由流水帳重放
    得出,任何時候都有值;`market_value`/`unrealized_pnl`/`total_pnl` 需要當日收盤價,
    階段 1(無歷史股價)為 None,階段 3 每日快照累積後才填入。
    """

    date: Date
    cost_basis: Decimal
    realized_pnl: Decimal
    quantity: Decimal | None = None  # 個股層級才有意義(持股股數);帳戶/組合彙總為 None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    total_pnl: Decimal | None = None


class StockHistory(BaseModel):
    """單一股票的時間序(供個股篩選/堆疊圖)。points 從該股第一次進場日起算。"""

    stock_code: str
    stock_name: str
    points: list[HistoryPoint] = []


class AccountHistory(BaseModel):
    """單一帳戶(分頁)的時間序;points 對齊整體日期軸,stocks 為該帳戶各檔個股序列。"""

    tab_name: str
    points: list[HistoryPoint] = []
    stocks: list[StockHistory] = []


class TransactionEvent(BaseModel):
    """單筆交易事件(供交易分布/散點圖、明細篩選用)。"""

    date: Date
    tab_name: str
    action: TransactionAction
    stock_code: str
    stock_name: str
    quantity: Decimal | None = None
    amount: Decimal | None = None


class PortfolioHistory(BaseModel):
    """整份試算表重建出的時間序:組合彙總 + 各帳戶 + 交易事件。

    `has_market_data` 表示 market_value 等欄位是否有值(需歷史股價);階段 1 為 False。
    """

    points: list[HistoryPoint] = []
    accounts: list[AccountHistory] = []
    events: list[TransactionEvent] = []
    has_market_data: bool = False


class HistoryResponse(BaseModel):
    """`GET /liff/history` 回應 — 動態圖表網頁的時間序資料來源。"""

    linked: bool
    status: FriendStatus | None = None
    history: PortfolioHistory | None = None


class LiffDashboardResponse(BaseModel):
    """`GET /liff/dashboard-data` 回應 — summary + history 合併。

    儀表板原本分別打 `/liff/summary` 與 `/liff/history`(各驗一次 id_token、各讀整張表
    一次);合併成單一端點後驗一次、讀一次,故把兩者欄位併在同一個回應。
    """

    linked: bool
    status: FriendStatus | None = None
    accounts: list[AccountSummary] = []
    history: PortfolioHistory | None = None
