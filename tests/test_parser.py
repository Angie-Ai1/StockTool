from decimal import Decimal

from app.models.schemas import TransactionAction
from app.services.parser import parse_transaction_text


def test_buy_with_quantity_and_amount_in_shares():
    result = parse_transaction_text("買入 鴻海 50股 13550元")
    assert result.errors == []
    txn = result.transactions[0]
    assert txn.action is TransactionAction.BUY
    assert txn.stock_query == "鴻海"
    assert txn.quantity == Decimal("50")
    assert txn.amount == Decimal("13550")
    assert txn.unit_price == Decimal("271.00")


def test_buy_with_quantity_in_lots_converts_to_shares():
    result = parse_transaction_text("買入 鴻海 1張 27100元")
    txn = result.transactions[0]
    assert txn.quantity == Decimal("1000")
    assert txn.unit_price == Decimal("27.10")


def test_buy_amount_only_estimates_quantity_from_closing_price():
    result = parse_transaction_text(
        "買入 鴻海 3000元", closing_price_lookup=lambda _stock: Decimal("150")
    )
    assert result.errors == []
    txn = result.transactions[0]
    assert txn.quantity == Decimal("20.00")
    assert txn.amount == Decimal("3000")


def test_buy_amount_only_without_price_source_is_a_parse_error():
    result = parse_transaction_text("買入 鴻海 3000元")
    assert result.transactions == []
    assert "收盤價" in result.errors[0].reason


def test_buy_amount_only_unknown_price_is_a_parse_error():
    result = parse_transaction_text(
        "買入 鴻海 3000元", closing_price_lookup=lambda _stock: None
    )
    assert result.transactions == []
    assert "查不到" in result.errors[0].reason


def test_sell_uses_same_field_order_as_buy():
    result = parse_transaction_text("賣出 鴻海 50股 14000元")
    txn = result.transactions[0]
    assert txn.action is TransactionAction.SELL


def test_sell_amount_only_leaves_quantity_none_for_sell_all():
    # 賣出只給金額 = 賣掉全部持股，股數留 None（不可用收盤價回推，否則會賣超）；
    # 不需要 closing_price_lookup 也能解析成功
    result = parse_transaction_text("賣 3006 12000")
    assert result.errors == []
    txn = result.transactions[0]
    assert txn.action is TransactionAction.SELL
    assert txn.quantity is None
    assert txn.amount == Decimal("12000")


def test_cash_dividend_format():
    result = parse_transaction_text("配息 華邦電 500元")
    txn = result.transactions[0]
    assert txn.action is TransactionAction.DIVIDEND
    assert txn.stock_query == "華邦電"
    assert txn.amount == Decimal("500")
    assert txn.quantity is None


def test_stock_dividend_format():
    result = parse_transaction_text("配股 華邦電 100股")
    txn = result.transactions[0]
    assert txn.action is TransactionAction.STOCK_DIVIDEND
    assert txn.quantity == Decimal("100")
    assert txn.amount is None


def test_account_tag_is_recognized_and_stripped():
    result = parse_transaction_text("個人/買入 鴻海 50 13550")
    txn = result.transactions[0]
    assert txn.account_tag == "個人"
    assert txn.action is TransactionAction.BUY
    assert txn.stock_query == "鴻海"


def test_batch_lines_partial_failure_does_not_discard_other_lines():
    text = "買入 鴻海 50股 13550元\n打錯了\n賣出 鴻海 10股 2800元"
    result = parse_transaction_text(text)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1
    assert result.errors[0].line_number == 2


def test_unrecognized_action_keyword_is_a_parse_error():
    result = parse_transaction_text("持有 鴻海 50股 13550元")
    assert result.transactions == []
    assert "無法辨識的動作關鍵字" in result.errors[0].reason


def test_blank_lines_are_skipped_without_error():
    result = parse_transaction_text("買入 鴻海 50股 13550元\n\n   \n")
    assert len(result.transactions) == 1
    assert result.errors == []
