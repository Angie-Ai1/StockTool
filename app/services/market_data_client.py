"""TWSE(上市)/TPEx(上櫃)證券清單與收盤價 — 規格 6.1。

只負責抓取與正規化成 StockQuote,不含快取或「今天是否已執行」的排程判斷
(那部分屬 1.9 tick.py,由它決定何時呼叫、快取多久)。
"""

from decimal import Decimal, InvalidOperation

import httpx

from app.models.schemas import StockQuote

TWSE_STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
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
    response = client.get(TWSE_STOCK_DAY_ALL_URL)
    response.raise_for_status()
    return [
        StockQuote(code=row["Code"], name=row["Name"], close=_to_decimal(row.get("ClosingPrice")))
        for row in response.json()
    ]


def fetch_tpex_listing(client: httpx.Client) -> list[StockQuote]:
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
    """合併上市 + 上櫃清單,供 fuzzy_match 比對與 1.9 收盤價任務共用。"""
    with httpx.Client(timeout=10) as client:
        return fetch_twse_listing(client) + fetch_tpex_listing(client)
