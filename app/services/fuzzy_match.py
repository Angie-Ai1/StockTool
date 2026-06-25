"""股票代碼/名稱模糊比對 — 規格 4.5、9.4。

代碼精確比對(O(1) 字典)優先,查不到才 fallback `thefuzz` 對名稱模糊比對,
避免每次都對近 2000 檔股票全量跑模糊演算法。股票清單由呼叫端注入
(來自 market_data_client.fetch_stock_list(),經 1.9 快取後傳入),這裡只做比對。
"""

from thefuzz import process

from app.models.schemas import StockQuote

FUZZY_MATCH_THRESHOLD = 70


def resolve_stock(query: str, stock_list: list[StockQuote]) -> StockQuote:
    """依 query(代碼或部分中文名稱)從 stock_list 找出對應證券。

    找不到時 raise ValueError,訊息可直接作為使用者錯誤提示——規格 4.7
    「解析不出來在旁邊狀態欄標記⚠️無法辨識,請修正」。
    """
    query = query.strip()
    if not query:
        raise ValueError("股票代碼/名稱不可為空白")

    by_code = {stock.code: stock for stock in stock_list}
    if query in by_code:
        return by_code[query]

    name_to_stock = {stock.name: stock for stock in stock_list}
    best_match = process.extractOne(query, name_to_stock.keys())
    if best_match is None or best_match[1] < FUZZY_MATCH_THRESHOLD:
        raise ValueError(f"查無此股票「{query}」,請確認代碼或名稱後重新輸入")
    matched_name, _score = best_match
    return name_to_stock[matched_name]
