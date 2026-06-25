from decimal import Decimal

import httpx

from app.services.market_data_client import (
    TPEX_MAINBOARD_DAILY_CLOSE_QUOTES_URL,
    TWSE_STOCK_DAY_ALL_URL,
    fetch_stock_list,
    fetch_tpex_listing,
    fetch_twse_listing,
)

TWSE_SAMPLE = [
    {"Code": "2330", "Name": "台積電", "ClosingPrice": "1,080.00"},
    {"Code": "2317", "Name": "鴻海", "ClosingPrice": "271"},
]

TPEX_SAMPLE = [
    {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "600.50"},
    {"SecuritiesCompanyCode": "5278", "CompanyName": "尚未開盤股", "Close": "--"},
]


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TWSE_STOCK_DAY_ALL_URL):
            return httpx.Response(200, json=TWSE_SAMPLE)
        if request.url == httpx.URL(TPEX_MAINBOARD_DAILY_CLOSE_QUOTES_URL):
            return httpx.Response(200, json=TPEX_SAMPLE)
        raise AssertionError(f"未預期的請求 {request.url}")

    return httpx.MockTransport(handler)


def test_fetch_twse_listing_parses_code_name_and_close():
    with httpx.Client(transport=_mock_transport()) as client:
        quotes = fetch_twse_listing(client)
    assert quotes[0].code == "2330"
    assert quotes[0].name == "台積電"
    assert quotes[0].close == Decimal("1080.00")


def test_fetch_tpex_listing_treats_dash_close_as_none():
    with httpx.Client(transport=_mock_transport()) as client:
        quotes = fetch_tpex_listing(client)
    assert quotes[0].close == Decimal("600.50")
    assert quotes[1].close is None


def test_fetch_stock_list_combines_twse_and_tpex(monkeypatch):
    real_client_cls = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client_cls(transport=_mock_transport())
    )
    quotes = fetch_stock_list()
    assert len(quotes) == 4
    assert {q.code for q in quotes} == {"2330", "2317", "6488", "5278"}
