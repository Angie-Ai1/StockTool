from datetime import date as Date
from decimal import Decimal

from app.models.schemas import StockQuote, TransactionAction, TransactionRow
from app.services.history_engine import reconstruct_history


def _row(day, action, query, qty=None, amount=None):
    return TransactionRow(
        row_uuid=f"u-{day}-{query}-{action.value}",
        date=day,
        action=action,
        stock_query=query,
        quantity=Decimal(qty) if qty is not None else None,
        amount=Decimal(amount) if amount is not None else None,
    )


def _resolver(mapping):
    def resolve(query):
        if query in mapping:
            return StockQuote(code=mapping[query][0], name=mapping[query][1])
        return None

    return resolve


RESOLVE = _resolver({"2330": ("2330", "台積電"), "台積電": ("2330", "台積電"), "0050": ("0050", "元大台灣50")})


def test_empty_input_returns_empty_history():
    result = reconstruct_history({}, RESOLVE)
    assert result.points == []
    assert result.accounts == []
    assert result.has_market_data is False


def test_single_buy_builds_cost_basis_point():
    txns = {"永豐": [_row(Date(2026, 1, 2), TransactionAction.BUY, "2330", qty="10", amount="10000")]}
    result = reconstruct_history(txns, RESOLVE)

    assert len(result.points) == 1
    point = result.points[0]
    assert point.date == Date(2026, 1, 2)
    assert point.cost_basis == Decimal("10000")
    assert point.realized_pnl == Decimal("0")
    assert point.market_value is None  # 階段 1 無歷史股價


def test_buy_then_sell_realizes_pnl_over_time():
    txns = {
        "永豐": [
            _row(Date(2026, 1, 2), TransactionAction.BUY, "2330", qty="10", amount="10000"),
            _row(Date(2026, 1, 10), TransactionAction.SELL, "2330", qty="5", amount="6000"),
        ]
    }
    result = reconstruct_history(txns, RESOLVE)

    assert [p.date for p in result.points] == [Date(2026, 1, 2), Date(2026, 1, 10)]
    # 賣 5 股,成本 5000,賣得 6000 → 已實現 +1000;剩 5 股成本 5000
    assert result.points[0].realized_pnl == Decimal("0")
    assert result.points[1].realized_pnl == Decimal("1000")
    assert result.points[1].cost_basis == Decimal("5000")
    # 個股層級序列帶持股股數;組合彙總層級為 None
    stock = result.accounts[0].stocks[0]
    assert stock.points[-1].quantity == Decimal("5")
    assert result.points[-1].quantity is None


def test_unrecognized_stock_row_is_skipped():
    txns = {"永豐": [_row(Date(2026, 1, 2), TransactionAction.BUY, "不存在股", qty="10", amount="10000")]}
    result = reconstruct_history(txns, RESOLVE)
    assert result.points == []


def test_oversell_row_is_skipped_without_polluting():
    txns = {
        "永豐": [
            _row(Date(2026, 1, 2), TransactionAction.BUY, "2330", qty="10", amount="10000"),
            _row(Date(2026, 1, 5), TransactionAction.SELL, "2330", qty="99", amount="1"),
        ]
    }
    result = reconstruct_history(txns, RESOLVE)
    # 賣超列被略過,庫存維持 10 股、已實現 0
    assert result.points[-1].cost_basis == Decimal("10000")
    assert result.points[-1].realized_pnl == Decimal("0")


def test_multiple_accounts_aggregate_on_shared_axis():
    txns = {
        "永豐": [_row(Date(2026, 1, 2), TransactionAction.BUY, "2330", qty="10", amount="10000")],
        "國泰": [_row(Date(2026, 1, 6), TransactionAction.BUY, "0050", qty="100", amount="15000")],
    }
    result = reconstruct_history(txns, RESOLVE)

    # 組合彙總日期軸 = 兩帳戶日期聯集,且每個帳戶序列長度一致
    assert [p.date for p in result.points] == [Date(2026, 1, 2), Date(2026, 1, 6)]
    assert all(len(a.points) == 2 for a in result.accounts)
    # 1/6 當天兩帳戶成本相加
    assert result.points[1].cost_basis == Decimal("25000")


def test_price_history_fills_market_value_and_unrealized():
    txns = {"永豐": [_row(Date(2026, 1, 2), TransactionAction.BUY, "2330", qty="10", amount="10000")]}
    prices = {"2330": {Date(2026, 1, 2): Decimal("1000"), Date(2026, 1, 3): Decimal("1100")}}
    result = reconstruct_history(txns, RESOLVE, price_history=prices)

    assert result.has_market_data is True
    # 日期軸延伸到最後報價日 1/3(無交易日仍有市值點,靠往前補值)
    assert [p.date for p in result.points] == [Date(2026, 1, 2), Date(2026, 1, 3)]
    assert result.points[0].market_value == Decimal("10000")  # 10 股 × 1000
    assert result.points[1].market_value == Decimal("11000")  # 10 股 × 1100
    assert result.points[1].unrealized_pnl == Decimal("1000")
    assert result.points[1].total_pnl == Decimal("1000")


def test_events_emitted_for_recognized_rows_only():
    txns = {
        "永豐": [
            _row(Date(2026, 1, 2), TransactionAction.BUY, "台積電", qty="10", amount="10000"),
            _row(Date(2026, 1, 3), TransactionAction.BUY, "不存在股", qty="1", amount="1"),
        ]
    }
    result = reconstruct_history(txns, RESOLVE)
    assert len(result.events) == 1
    assert result.events[0].stock_code == "2330"
    assert result.events[0].stock_name == "台積電"
