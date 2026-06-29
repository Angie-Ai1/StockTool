"""損益引擎 — 移動加權平均成本法,規格第 5 章。

MVP 階段不計手續費/證交稅(5.1),金額全面使用 Decimal。
"""

from decimal import Decimal

from app.models.schemas import ParsedTransaction, Position, TransactionAction


class InsufficientPositionError(ValueError):
    """賣出數量超過目前庫存 — 規格 5.5 賣超防呆"""


def apply_buy(position: Position, quantity: Decimal, amount: Decimal) -> Position:
    """買進均價 = (原總成本 + 這次買進金額) ÷ (原股數 + 這次買進股數) — 規格 5.2"""
    if quantity <= 0 or amount <= 0:
        raise ValueError("數量與金額必須大於 0")
    total_cost = position.avg_cost * position.quantity + amount
    new_quantity = position.quantity + quantity
    return position.model_copy(
        update={"quantity": new_quantity, "avg_cost": total_cost / new_quantity}
    )


def apply_sell(position: Position, quantity: Decimal, amount: Decimal) -> Position:
    """用當下均價計算這次賣出成本,已實現損益立即結算,均價本身不變 — 規格 5.2、5.3、5.5"""
    if quantity <= 0 or amount <= 0:
        raise ValueError("數量與金額必須大於 0")
    if quantity > position.quantity:
        raise InsufficientPositionError(f"賣出數量 {quantity} 超過目前庫存 {position.quantity}")
    cost_of_sold = position.avg_cost * quantity
    realized = amount - cost_of_sold
    return position.model_copy(
        update={
            "quantity": position.quantity - quantity,
            "realized_pnl": position.realized_pnl + realized,
        }
    )


def apply_stock_dividend(position: Position, quantity: Decimal) -> Position:
    """配股:股數增加,均價依增加後股數重新計算,總成本不變 — 規格 5.4"""
    if quantity <= 0:
        raise ValueError("股數必須大於 0")
    total_cost = position.avg_cost * position.quantity
    new_quantity = position.quantity + quantity
    return position.model_copy(
        update={"quantity": new_quantity, "avg_cost": total_cost / new_quantity}
    )


def apply_cash_dividend(position: Position, amount: Decimal) -> Position:
    """配息:扣減整體投入本金,均價隨之下降,股數不變 — 規格 5.4"""
    if amount <= 0:
        raise ValueError("金額必須大於 0")
    if position.quantity <= 0:
        raise ValueError("目前無庫存,無法套用配息")
    total_cost = max(position.avg_cost * position.quantity - amount, Decimal("0"))
    return position.model_copy(update={"avg_cost": total_cost / position.quantity})


def compute_unrealized_pnl(position: Position, closing_price: Decimal) -> Decimal:
    """依每日收盤價動態計算目前庫存的未實現損益 — 規格 5.3"""
    return (closing_price - position.avg_cost) * position.quantity


def apply_transaction(position: Position, txn: ParsedTransaction) -> Position:
    """依 ParsedTransaction.action 分派到對應的均價/損益更新規則"""
    if txn.action is TransactionAction.BUY:
        if txn.quantity is None or txn.amount is None:
            raise ValueError("買進需要數量與金額")
        return apply_buy(position, txn.quantity, txn.amount)
    if txn.action is TransactionAction.SELL:
        if txn.quantity is None or txn.amount is None:
            raise ValueError("賣出需要數量與金額")
        return apply_sell(position, txn.quantity, txn.amount)
    if txn.action is TransactionAction.STOCK_DIVIDEND:
        if txn.quantity is None:
            raise ValueError("配股需要股數")
        return apply_stock_dividend(position, txn.quantity)
    if txn.amount is None:
        raise ValueError("配息需要金額")
    return apply_cash_dividend(position, txn.amount)
