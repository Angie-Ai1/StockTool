"""`/tick` 排程進入點 — 規格 1.9:單一外部 Cron(防休眠 + 14:30 收盤任務合併)。

收到請求立即回 200、重任務丟 `BackgroundTasks` 背景執行、判斷是否已過今日 14:30
(Asia/Taipei)且尚未執行過、抓收盤價/代碼清單並快取、逐位 active 親友依序呼叫
`sheets_client.resync()`(留間隔避免撞 Sheets API per-project 共用配額)、標記今日
已執行。

身分驗證採共享密鑰(`.env` `TICK_SHARED_SECRET`,Cloud Scheduler 帶在
`X-Tick-Secret` header)——決策理由與「之後若要換成 OIDC token 驗證」的替代方案
見 `openspecs/DE.md`。
"""

import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from googleapiclient.errors import HttpError

from app.config import get_settings
from app.db.firestore_client import get_firestore_client
from app.models.schemas import SchedulerState, StockQuote
from app.services.friend_repository import list_active_friends
from app.services.market_data_client import fetch_stock_list
from app.services.oauth_service import OAuthInvalidGrantError
from app.services.sheets_client import resync

router = APIRouter()

CLOSE_TASK_HOUR = 14
CLOSE_TASK_MINUTE = 30

# 留間隔避免逐位親友 resync 一次性發出全部請求,撞到 Sheets API per-project 共用配額
# (約 300 次/分鐘,所有親友加總)——master spec 第 9 章、技術文件第 4 章。
FRIEND_RESYNC_INTERVAL_SECONDS = 2

# 14:30 收盤任務抓回的收盤價/代碼清單,留給之後逐位親友 resync 共用(1.6)。
# 記憶體快取;重啟或 14:30 前為空時,從 Firestore system/stock_list 回載。
_cached_stock_list: list[StockQuote] = []


def get_cached_stock_list() -> list[StockQuote]:
    global _cached_stock_list
    if not _cached_stock_list:
        _cached_stock_list = _load_stock_list_from_firestore()
    return _cached_stock_list


def _is_past_close_task_time(now: datetime) -> bool:
    return (now.hour, now.minute) >= (CLOSE_TASK_HOUR, CLOSE_TASK_MINUTE)


def get_scheduler_state(firestore_client=None) -> SchedulerState:
    client = firestore_client or get_firestore_client()
    snapshot = client.collection("system").document("scheduler").get()
    if not snapshot.exists:
        return SchedulerState()
    return SchedulerState.model_validate(snapshot.to_dict())


def _mark_today_executed(today: str, firestore_client=None) -> None:
    client = firestore_client or get_firestore_client()
    client.collection("system").document("scheduler").set({"last_run_date": today})


def _save_stock_list_to_firestore(stock_list: list[StockQuote], firestore_client=None) -> None:
    client = firestore_client or get_firestore_client()
    client.collection("system").document("stock_list").set({
        "stocks": [
            {"code": s.code, "name": s.name, "close": str(s.close) if s.close is not None else None}
            for s in stock_list
        ]
    })


def _load_stock_list_from_firestore(firestore_client=None) -> list[StockQuote]:
    client = firestore_client or get_firestore_client()
    snapshot = client.collection("system").document("stock_list").get()
    if not snapshot.exists:
        return []
    data = snapshot.to_dict()
    return [
        StockQuote(
            code=s["code"],
            name=s["name"],
            close=Decimal(s["close"]) if s.get("close") is not None else None,
        )
        for s in data.get("stocks", [])
    ]


def run_daily_close_task(
    *,
    now: datetime | None = None,
    firestore_client=None,
    fetch_stock_list_fn=fetch_stock_list,
    save_stock_list_fn=_save_stock_list_to_firestore,
    list_friends_fn=list_active_friends,
    resync_fn=resync,
    sleep_fn=time.sleep,
) -> None:
    """還沒過 14:30、或今天已經跑過,直接 return;否則抓收盤價清單、逐位 active 親友
    依序 resync、標記今日已執行——規格 1.9。

    單一親友碰到 OAuth 失效或 Sheets API 404(`resync()` 內部已標記
    `needs_reauth` 並往上拋例外)時,跳過這位親友繼續處理下一位,不讓一位親友的
    問題擋下整批排程;其他未預期的例外則直接往上拋,讓背景任務中斷在那一步——
    今日執行旗標也就不會被標記,下次 `/tick` 會整批重來一次(resync 本身是
    cache-aside 設計,重做一次是安全的,不會造成資料重複)。
    """
    settings = get_settings()
    current = now or datetime.now(ZoneInfo(settings.tz))
    if not _is_past_close_task_time(current):
        return

    today = current.date().isoformat()
    client = firestore_client or get_firestore_client()
    state = get_scheduler_state(firestore_client=client)
    if state.last_run_date == today:
        return

    global _cached_stock_list
    _cached_stock_list = fetch_stock_list_fn()
    save_stock_list_fn(_cached_stock_list, firestore_client=client)

    friends = list_friends_fn(firestore_client=client)
    for index, friend in enumerate(friends):
        if index > 0:
            sleep_fn(FRIEND_RESYNC_INTERVAL_SECONDS)
        try:
            resync_fn(friend, _cached_stock_list, firestore_client=client)
        except (OAuthInvalidGrantError, HttpError):
            continue

    _mark_today_executed(today, firestore_client=client)


@router.api_route("/tick", methods=["GET", "POST"])
def tick(background_tasks: BackgroundTasks, x_tick_secret: str | None = Header(default=None)) -> dict[str, str]:
    settings = get_settings()
    if not settings.tick_shared_secret or x_tick_secret != settings.tick_shared_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    background_tasks.add_task(run_daily_close_task)
    return {"status": "ok"}
