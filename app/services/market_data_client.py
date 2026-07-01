"""TWSE(上市)/TPEx(上櫃)證券清單與收盤價 — 規格 6.1。

只負責抓取與正規化成 StockQuote,不含快取或「今天是否已執行」的排程判斷
(那部分屬 1.9 tick.py,由它決定何時呼叫、快取多久)。
"""

from decimal import Decimal, InvalidOperation

import httpx

from app.models.schemas import StockQuote

# 上市改用官網即時 JSON，上櫃維持極速穩定的 OpenAPI
TWSE_MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999"
TPEX_MAINBOARD_DAILY_CLOSE_QUOTES_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
)


def _to_decimal(value: str | None) -> Decimal | None:
    if not value or value == "--":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def fetch_twse_listing(client: httpx.Client) -> list[StockQuote]:
    """改爬官網 MI_INDEX JSON API，收盤後即時更新 (14:30 前後即可拿到當日最新)"""
    response = client.get(TWSE_MI_INDEX_URL)
    response.raise_for_status()
    data = response.json()
    
    # 尋找含有個股收盤行情的 table (通常是 Table 8)
    stock_table = None
    for table in data.get("tables", []):
        title = table.get("title", "")
        if "每日收盤行情" in title:
            stock_table = table
            break
            
    if not stock_table:
        # 如果結構變更或查無資料，退回空清單以容錯
        return []
        
    quotes = []
    # 欄位順序：0=證券代號, 1=證券名稱, 8=收盤價
    for row in stock_table.get("data", []):
        code = row[0].strip()
        name = row[1].strip()
        close_val = row[8].strip()
        quotes.append(
            StockQuote(
                code=code,
                name=name,
                close=_to_decimal(close_val)
            )
        )
    return quotes


def fetch_tpex_listing(client: httpx.Client) -> list[StockQuote]:
    """維持原樣：上櫃 OpenAPI 更新迅速且穩定"""
    response = client.get(TPEX_MAINBOARD_DAILY_CLOSE_QUOTES_URL)
    response.raise_for_status()
    return [
        StockQuote(
            code=row["SecuritiesCompanyCode"],
            name=row["CompanyName"],
            close=_to_decimal(row.get("Close")),
        )
        for row in response.json()
    ]


def fetch_stock_list() -> list[StockQuote]:
    """合併上市 + 上櫃清單,供 fuzzy_match 比對與 1.9 收盤價任務共用。

    個別來源容錯:單一交易所(TWSE/TPEx)抓取失敗時,仍回傳另一個來源的清單,
    避免一邊暫時故障就讓整批收盤價更新停擺。兩邊都失敗才往上拋,讓 tick 中止並於
    下次重試——不會用空清單去 resync,以免把每列都標成「無法辨識」回寫試算表。
    """
    quotes: list[StockQuote] = []
    errors: list[Exception] = []
    with httpx.Client(timeout=10) as client:
        for fetch in (fetch_twse_listing, fetch_tpex_listing):
            try:
                quotes.extend(fetch(client))
            except Exception as exc:  # noqa: BLE001 — 單一來源故障不應拖垮另一來源
                errors.append(exc)
    if not quotes and errors:
        raise errors[0]
    return quotes