"""時間序歷史引擎 — 動態圖表網頁(階段 1)。

把每個帳戶分頁的流水帳「逐筆重放」,在整體日期軸上的每一天記一個累積快照,
重放規則與 `sheets_client.resync_account_tab` 完全一致(同樣用 pnl_engine、同樣
跳過無法辨識/賣超的列),確保圖表上的數字跟試算表統計摘要對得起來。

純函式、不打任何外部 API:把「讀試算表」與「重建時間序」切開,讓引擎能用模擬資料
獨立測試與預覽(見 `scripts/generate_mock_history.py`)。

`cost_basis`(持倉成本)與 `realized_pnl`(累積已實現損益)只靠流水帳就能算;
`market_value`/`unrealized_pnl`/`total_pnl` 需要「當日」收盤價,階段 1 沒有歷史股價
時傳 `price_history=None`,這些欄位留空(None),等階段 3 每日快照累積後才有值。
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import date as Date
from decimal import Decimal
from typing import Callable

from app.models.schemas import (
    AccountHistory,
    HistoryPoint,
    ParsedTransaction,
    PortfolioHistory,
    Position,
    StockHistory,
    StockQuote,
    TransactionEvent,
    TransactionRow,
)
from app.services.pnl_engine import InsufficientPositionError, apply_transaction

ZERO = Decimal("0")

# query(原始股票文字)→ 解析後的 StockQuote;認不出來回 None(該列略過,比照 resync)
Resolver = Callable[[str], StockQuote | None]

# code → {日期: 當日收盤價};用於 market_value/未實現損益(階段 3 / 模擬預覽才會帶入)
PriceHistory = dict[str, dict[Date, Decimal]]


def _build_price_lookup(price_history: PriceHistory | None):
    """回傳 `(code, date) -> Decimal | None` 的查價函式,採「往前補值」:

    某天若無報價(假日/尚未開始記錄),取該日之前最近一筆已知收盤價,避免曲線斷裂。
    """
    prepared: dict[str, tuple[list[Date], list[Decimal]]] = {}
    for code, by_date in (price_history or {}).items():
        items = sorted(by_date.items())
        prepared[code] = ([d for d, _ in items], [p for _, p in items])

    def lookup(code: str, day: Date) -> Decimal | None:
        entry = prepared.get(code)
        if entry is None:
            return None
        dates, prices = entry
        idx = bisect.bisect_right(dates, day) - 1
        return prices[idx] if idx >= 0 else None

    return lookup


def _snapshot(
    positions: dict[str, Position], day: Date, price_at, has_market: bool
) -> HistoryPoint:
    """把某帳戶在 `day` 的所有持倉彙總成一個快照點。"""
    cost_basis = ZERO
    market_value = ZERO
    realized = ZERO
    for code, pos in positions.items():
        realized += pos.realized_pnl
        cost_basis += pos.avg_cost * pos.quantity
        if has_market and pos.quantity > 0:
            price = price_at(code, day)
            if price is not None:
                market_value += price * pos.quantity

    point = HistoryPoint(date=day, cost_basis=cost_basis, realized_pnl=realized)
    if has_market:
        point.market_value = market_value
        point.unrealized_pnl = market_value - cost_basis
        point.total_pnl = realized + (market_value - cost_basis)
    return point


def _stock_snapshot(pos: Position, day: Date, price_at, has_market: bool) -> HistoryPoint:
    cost_basis = pos.avg_cost * pos.quantity
    point = HistoryPoint(
        date=day, cost_basis=cost_basis, realized_pnl=pos.realized_pnl, quantity=pos.quantity
    )
    if has_market:
        market_value = ZERO
        if pos.quantity > 0:
            price = price_at(pos.stock_code, day)
            if price is not None:
                market_value = price * pos.quantity
        point.market_value = market_value
        point.unrealized_pnl = market_value - cost_basis
        point.total_pnl = pos.realized_pnl + (market_value - cost_basis)
    return point


def reconstruct_history(
    transactions_by_account: dict[str, list[TransactionRow]],
    resolve: Resolver,
    *,
    price_history: PriceHistory | None = None,
    end_date: Date | None = None,
) -> PortfolioHistory:
    """重建整份試算表的時間序。

    參數:
      transactions_by_account: {分頁名稱: 該分頁流水帳列(依試算表順序)}
      resolve: 把「股票代碼/名稱」原始文字解析成 StockQuote,認不出回 None(略過該列)
      price_history: 各檔每日收盤價;None 代表階段 1 無歷史股價(只算成本/已實現)
      end_date: 日期軸結束日(預設取最後一筆交易日;有 price_history 時延伸到最後報價日)

    回傳 PortfolioHistory:組合彙總序列 + 各帳戶序列 + 交易事件清單。
    """
    has_market = bool(price_history)
    price_at = _build_price_lookup(price_history)

    # 預先解析每筆交易、依帳戶與日期分組,並收集整體日期軸
    grouped_by_account: dict[str, dict[Date, list[tuple[str, TransactionRow]]]] = {}
    events: list[TransactionEvent] = []
    stock_names: dict[str, str] = {}
    date_set: set[Date] = set()

    for tab, rows in transactions_by_account.items():
        grouped: dict[Date, list[tuple[str, TransactionRow]]] = defaultdict(list)
        for row in rows:
            stock = resolve(row.stock_query)
            if stock is None:
                continue  # 認不出的列不計入(比照 resync 的 STATUS_INVALID_ROW)
            stock_names[stock.code] = stock.name
            grouped[row.date].append((stock.code, row))
            date_set.add(row.date)
            events.append(
                TransactionEvent(
                    date=row.date,
                    tab_name=tab,
                    action=row.action,
                    stock_code=stock.code,
                    stock_name=stock.name,
                    quantity=row.quantity,
                    amount=row.amount,
                )
            )
        grouped_by_account[tab] = grouped

    if has_market:
        for by_date in price_history.values():  # type: ignore[union-attr]
            date_set.update(by_date.keys())

    if not date_set:
        return PortfolioHistory(has_market_data=has_market)

    dates = sorted(d for d in date_set if end_date is None or d <= end_date)

    accounts_out: list[AccountHistory] = []
    for tab, grouped in grouped_by_account.items():
        positions: dict[str, Position] = {}
        account_points: list[HistoryPoint] = []
        stock_points: dict[str, list[HistoryPoint]] = defaultdict(list)
        active_codes: list[str] = []  # 已進場、需要逐日記點的個股(保持順序)

        for day in dates:
            for code, row in grouped.get(day, []):
                pos = positions.get(code, Position(stock_code=code))
                parsed = ParsedTransaction(
                    raw_text="",
                    action=row.action,
                    stock_query=row.stock_query,
                    quantity=row.quantity,
                    amount=row.amount,
                )
                try:
                    positions[code] = apply_transaction(pos, parsed)
                except (InsufficientPositionError, ValueError):
                    continue  # 賣超/格式錯誤的列不污染後面計算(比照 resync)
                if code not in active_codes:
                    active_codes.append(code)

            account_points.append(_snapshot(positions, day, price_at, has_market))
            for code in active_codes:
                pos = positions.get(code, Position(stock_code=code))
                stock_points[code].append(_stock_snapshot(pos, day, price_at, has_market))

        stocks_out = [
            StockHistory(stock_code=code, stock_name=stock_names.get(code, code), points=points)
            for code, points in stock_points.items()
        ]
        accounts_out.append(
            AccountHistory(tab_name=tab, points=account_points, stocks=stocks_out)
        )

    portfolio_points = _aggregate_portfolio(accounts_out, dates, has_market)

    return PortfolioHistory(
        points=portfolio_points,
        accounts=accounts_out,
        events=events,
        has_market_data=has_market,
    )


def _aggregate_portfolio(
    accounts: list[AccountHistory], dates: list[Date], has_market: bool
) -> list[HistoryPoint]:
    """各帳戶 points 已對齊同一條日期軸,逐日加總成組合彙總序列。"""
    portfolio: list[HistoryPoint] = []
    for i, day in enumerate(dates):
        cost_basis = sum((a.points[i].cost_basis for a in accounts), ZERO)
        realized = sum((a.points[i].realized_pnl for a in accounts), ZERO)
        point = HistoryPoint(date=day, cost_basis=cost_basis, realized_pnl=realized)
        if has_market:
            market_value = sum(((a.points[i].market_value or ZERO) for a in accounts), ZERO)
            point.market_value = market_value
            point.unrealized_pnl = market_value - cost_basis
            point.total_pnl = realized + (market_value - cost_basis)
        portfolio.append(point)
    return portfolio
