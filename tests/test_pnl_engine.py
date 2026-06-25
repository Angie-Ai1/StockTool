from decimal import Decimal

import pytest

from app.models.schemas import ParsedTransaction, Position, TransactionAction
from app.services.pnl_engine import (
    InsufficientPositionError,
    apply_buy,
    apply_cash_dividend,
    apply_sell,
    apply_stock_dividend,
    apply_transaction,
    compute_unrealized_pnl,
)


@pytest.fixture
def empty_position():
    return Position(stock_code="2330")


def test_apply_buy_from_empty_position(empty_position):
    position = apply_buy(empty_position, Decimal("50"), Decimal("13550"))
    assert position.quantity == Decimal("50")
    assert position.avg_cost == Decimal("271")


def test_apply_buy_recomputes_moving_average(empty_position):
    position = apply_buy(empty_position, Decimal("50"), Decimal("13550"))
    position = apply_buy(position, Decimal("50"), Decimal("15000"))
    assert position.quantity == Decimal("100")
    assert position.avg_cost == Decimal("285.50")


def test_apply_sell_realizes_pnl_immediately_without_changing_avg_cost():
    position = Position(stock_code="2330", quantity=Decimal("100"), avg_cost=Decimal("285.50"))
    position = apply_sell(position, Decimal("30"), Decimal("9000"))
    assert position.quantity == Decimal("70")
    assert position.avg_cost == Decimal("285.50")
    assert position.realized_pnl == Decimal("435.00")


def test_apply_sell_more_than_holding_is_blocked():
    position = Position(stock_code="2330", quantity=Decimal("10"), avg_cost=Decimal("100"))
    with pytest.raises(InsufficientPositionError):
        apply_sell(position, Decimal("11"), Decimal("1200"))


def test_apply_stock_dividend_increases_quantity_and_lowers_avg_cost():
    position = Position(stock_code="2330", quantity=Decimal("70"), avg_cost=Decimal("285.50"))
    position = apply_stock_dividend(position, Decimal("10"))
    assert position.quantity == Decimal("80")
    assert position.avg_cost == Decimal("249.8125")


def test_apply_cash_dividend_reduces_avg_cost_without_changing_quantity():
    position = Position(stock_code="2330", quantity=Decimal("80"), avg_cost=Decimal("249.8125"))
    position = apply_cash_dividend(position, Decimal("500"))
    assert position.quantity == Decimal("80")
    assert position.avg_cost == Decimal("243.5625")


def test_cash_dividend_on_empty_position_is_rejected(empty_position):
    with pytest.raises(ValueError):
        apply_cash_dividend(empty_position, Decimal("500"))


def test_compute_unrealized_pnl():
    position = Position(stock_code="2330", quantity=Decimal("80"), avg_cost=Decimal("243.5625"))
    pnl = compute_unrealized_pnl(position, Decimal("260"))
    assert pnl == Decimal("1315.0000")


def test_apply_transaction_dispatches_buy(empty_position):
    txn = ParsedTransaction(
        raw_text="買入 台積電 50股 13550元",
        action=TransactionAction.BUY,
        stock_query="台積電",
        quantity=Decimal("50"),
        amount=Decimal("13550"),
    )
    position = apply_transaction(empty_position, txn)
    assert position.quantity == Decimal("50")


def test_apply_transaction_dispatches_sell():
    position = Position(stock_code="2330", quantity=Decimal("50"), avg_cost=Decimal("271"))
    txn = ParsedTransaction(
        raw_text="賣出 台積電 20股 6000元",
        action=TransactionAction.SELL,
        stock_query="台積電",
        quantity=Decimal("20"),
        amount=Decimal("6000"),
    )
    position = apply_transaction(position, txn)
    assert position.quantity == Decimal("30")
    assert position.realized_pnl == Decimal("580")
