import pytest

from app.models.schemas import StockQuote
from app.services.fuzzy_match import resolve_stock

STOCK_LIST = [
    StockQuote(code="2330", name="台積電"),
    StockQuote(code="3711", name="日月光投控"),
    StockQuote(code="2317", name="鴻海"),
]


def test_resolve_by_exact_code():
    stock = resolve_stock("3711", STOCK_LIST)
    assert stock.code == "3711"
    assert stock.name == "日月光投控"


def test_resolve_by_exact_name():
    stock = resolve_stock("鴻海", STOCK_LIST)
    assert stock.code == "2317"


def test_resolve_by_partial_name_fuzzy_match():
    stock = resolve_stock("日月光", STOCK_LIST)
    assert stock.code == "3711"
    assert stock.name == "日月光投控"


def test_resolve_unknown_stock_raises_value_error_with_reason():
    with pytest.raises(ValueError, match="查無此股票"):
        resolve_stock("根本不存在的怪公司", STOCK_LIST)


def test_resolve_blank_query_raises_value_error():
    with pytest.raises(ValueError, match="不可為空白"):
        resolve_stock("   ", STOCK_LIST)
