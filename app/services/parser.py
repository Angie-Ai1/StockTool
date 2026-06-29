"""記帳文字解析 — 規格 4.2~4.7。

股票名稱/代碼的模糊比對留給 fuzzy_match.resolve_stock(),這裡只負責抽出
原始的 stock_query 字串,不做股票辨識。
"""

import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from app.models.schemas import ParsedTransaction, ParseError, ParseResult, TransactionAction

ACTION_KEYWORDS: dict[str, TransactionAction] = {
    "買": TransactionAction.BUY,
    "買入": TransactionAction.BUY,
    "買進": TransactionAction.BUY,
    "賣": TransactionAction.SELL,
    "賣出": TransactionAction.SELL,
    "配息": TransactionAction.DIVIDEND,
    "配股": TransactionAction.STOCK_DIVIDEND,
}

_NUMBER_UNIT_RE = re.compile(r"^([\d,]+(?:\.\d+)?)(.*)$")

ClosingPriceLookup = Callable[[str], Decimal | None]


def _split_number_unit(token: str) -> tuple[Decimal, str]:
    match = _NUMBER_UNIT_RE.match(token)
    if not match:
        raise ValueError(f"無法辨識的數字「{token}」")
    number_str, unit = match.groups()
    return Decimal(number_str.replace(",", "")), unit.strip()


def _parse_quantity(token: str) -> Decimal:
    number, unit = _split_number_unit(token)
    if unit in ("", "股"):
        quantity = number
    elif unit == "張":
        quantity = number * Decimal(1000)
    else:
        raise ValueError(f"無法辨識的數量單位「{unit}」")
    if quantity <= 0:
        raise ValueError("數量必須大於 0")
    return quantity


def _parse_amount(token: str) -> Decimal:
    number, unit = _split_number_unit(token)
    if unit not in ("", "元"):
        raise ValueError(f"無法辨識的金額單位「{unit}」")
    if number <= 0:
        raise ValueError("金額必須大於 0")
    return number


def _parse_line(line: str, closing_price_lookup: ClosingPriceLookup | None) -> ParsedTransaction:
    tokens = line.split()
    if not tokens:
        raise ValueError("空白行")

    account_tag: str | None = None
    if "/" in tokens[0]:
        account_tag, action_keyword = tokens[0].split("/", 1)
        tokens = [action_keyword, *tokens[1:]]

    action_keyword = tokens[0]
    action = ACTION_KEYWORDS.get(action_keyword)
    if action is None:
        raise ValueError(f"無法辨識的動作關鍵字「{action_keyword}」,請用 買/賣/配息/配股")

    rest = tokens[1:]

    if action in (TransactionAction.BUY, TransactionAction.SELL):
        if len(rest) == 3:
            stock_query, quantity_token, amount_token = rest
            quantity = _parse_quantity(quantity_token)
            amount = _parse_amount(amount_token)
        elif len(rest) == 2:
            stock_query, amount_token = rest
            amount = _parse_amount(amount_token)
            if action is TransactionAction.SELL:
                # 賣出只給金額 = 賣掉「目前全部持股」、實收金額為此數，賺賠由記帳時依
                # 庫存均價結算（不可用收盤價回推股數，否則賣超且損益錯誤）。股數留 None，
                # 於 _book_transactions 依當下庫存填上。
                quantity = None
            else:
                # 買進只給金額：依收盤價估算買到的股數
                if closing_price_lookup is None:
                    raise ValueError(
                        "只輸入金額,需要收盤價才能估算股數,目前無法取得收盤價,"
                        "請改用「個股 數量 金額」格式重新輸入"
                    )
                price = closing_price_lookup(stock_query)
                if price is None or price <= 0:
                    raise ValueError(f"查不到「{stock_query}」的收盤價,請改用「個股 數量 金額」格式重新輸入")
                quantity = (amount / price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            raise ValueError("格式不符,買/賣需要「個股 數量 金額」或「個股 金額」")

        unit_price = (
            (amount / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if quantity is not None
            else None
        )
        return ParsedTransaction(
            raw_text=line,
            action=action,
            stock_query=stock_query,
            quantity=quantity,
            amount=amount,
            account_tag=account_tag,
            unit_price=unit_price,
        )

    if action is TransactionAction.DIVIDEND:
        if len(rest) != 2:
            raise ValueError("格式不符,配息需要「個股 金額」")
        stock_query, amount_token = rest
        amount = _parse_amount(amount_token)
        return ParsedTransaction(
            raw_text=line,
            action=action,
            stock_query=stock_query,
            amount=amount,
            account_tag=account_tag,
        )

    if len(rest) != 2:
        raise ValueError("格式不符,配股需要「個股 股數」")
    stock_query, quantity_token = rest
    quantity = _parse_quantity(quantity_token)
    return ParsedTransaction(
        raw_text=line,
        action=action,
        stock_query=stock_query,
        quantity=quantity,
        account_tag=account_tag,
    )


def parse_transaction_text(
    text: str, closing_price_lookup: ClosingPriceLookup | None = None
) -> ParseResult:
    """解析整則訊息(可能多行批次)。部分行失敗不影響其他行——規格 4.5。"""
    result = ParseResult()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            result.transactions.append(_parse_line(line, closing_price_lookup))
        except ValueError as exc:
            result.errors.append(ParseError(line_number=line_number, raw_text=raw_line, reason=str(exc)))
    return result
