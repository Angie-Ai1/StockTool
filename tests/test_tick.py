from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.config import Settings
from app.models.schemas import FriendRecord, StockQuote
from app.routers import tick
from app.services.oauth_service import OAuthInvalidGrantError

DUMMY_SETTINGS = Settings(tick_shared_secret="dummy-secret", tz="Asia/Taipei")


@pytest.fixture(autouse=True)
def _dummy_settings(monkeypatch):
    monkeypatch.setattr(tick, "get_settings", lambda: DUMMY_SETTINGS)
    monkeypatch.setattr(tick, "_cached_stock_list", [])


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tick.router)
    return TestClient(app)


def _now(hour: int, minute: int) -> datetime:
    return datetime(2026, 6, 25, hour, minute, tzinfo=ZoneInfo("Asia/Taipei"))


def test_tick_rejects_missing_secret(client):
    response = client.get("/tick")
    assert response.status_code == 401


def test_tick_rejects_wrong_secret(client):
    response = client.get("/tick", headers={"X-Tick-Secret": "wrong"})
    assert response.status_code == 401


def test_tick_rejects_when_secret_not_configured(client, monkeypatch):
    monkeypatch.setattr(tick, "get_settings", lambda: Settings(tick_shared_secret=""))
    response = client.get("/tick", headers={"X-Tick-Secret": "anything"})
    assert response.status_code == 401


def test_tick_accepts_correct_secret_and_schedules_background_task(client, monkeypatch):
    run_task = MagicMock()
    monkeypatch.setattr(tick, "run_daily_close_task", run_task)

    response = client.get("/tick", headers={"X-Tick-Secret": "dummy-secret"})

    assert response.status_code == 200
    run_task.assert_called_once()


def test_get_scheduler_state_returns_default_when_missing():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=False)
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot

    state = tick.get_scheduler_state(firestore_client=firestore_client)

    assert state.last_run_date is None


def test_get_scheduler_state_parses_existing_doc():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"last_run_date": "2026-06-20"}
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot

    state = tick.get_scheduler_state(firestore_client=firestore_client)

    assert state.last_run_date == "2026-06-20"


def test_run_daily_close_task_skips_before_close_time():
    firestore_client = MagicMock()
    fetch_fn = MagicMock()

    tick.run_daily_close_task(now=_now(14, 29), firestore_client=firestore_client, fetch_stock_list_fn=fetch_fn)

    firestore_client.collection.assert_not_called()
    fetch_fn.assert_not_called()


def test_run_daily_close_task_skips_when_already_run_today():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"last_run_date": "2026-06-25"}
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot
    fetch_fn = MagicMock()

    tick.run_daily_close_task(now=_now(14, 30), firestore_client=firestore_client, fetch_stock_list_fn=fetch_fn)

    fetch_fn.assert_not_called()
    firestore_client.collection.return_value.document.return_value.set.assert_not_called()


def test_run_daily_close_task_runs_and_marks_executed_when_due():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=False)
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot
    stock_list = [StockQuote(code="2330", name="台積電", close=Decimal("1000"))]
    fetch_fn = MagicMock(return_value=stock_list)
    save_fn = MagicMock()

    tick.run_daily_close_task(
        now=_now(14, 30),
        firestore_client=firestore_client,
        fetch_stock_list_fn=fetch_fn,
        save_stock_list_fn=save_fn,
        list_friends_fn=MagicMock(return_value=[]),
    )

    fetch_fn.assert_called_once()
    save_fn.assert_called_once_with(stock_list, firestore_client=firestore_client)
    firestore_client.collection.return_value.document.return_value.set.assert_called_once_with(
        {"last_run_date": "2026-06-25"}
    )
    assert tick.get_cached_stock_list() == stock_list


def test_run_daily_close_task_skips_firestore_save_when_fetch_returns_empty():
    firestore_client = MagicMock()
    firestore_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    save_fn = MagicMock()

    tick.run_daily_close_task(
        now=_now(14, 30),
        firestore_client=firestore_client,
        fetch_stock_list_fn=MagicMock(return_value=[]),
        save_stock_list_fn=save_fn,
        list_friends_fn=MagicMock(return_value=[]),
    )

    save_fn.assert_not_called()


def test_save_stock_list_to_firestore_writes_correct_document():
    firestore_client = MagicMock()
    stock_list = [
        StockQuote(code="2330", name="台積電", close=Decimal("1000")),
        StockQuote(code="2408", name="南亞科", close=None),
    ]

    tick._save_stock_list_to_firestore(stock_list, firestore_client=firestore_client)

    firestore_client.collection.return_value.document.return_value.set.assert_called_once_with({
        "stocks": [
            {"code": "2330", "name": "台積電", "close": "1000"},
            {"code": "2408", "name": "南亞科", "close": None},
        ]
    })


def test_load_stock_list_from_firestore_returns_list_when_doc_exists():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {
        "stocks": [
            {"code": "2330", "name": "台積電", "close": "1000"},
            {"code": "2408", "name": "南亞科", "close": None},
        ]
    }
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot

    result = tick._load_stock_list_from_firestore(firestore_client=firestore_client)

    assert result == [
        StockQuote(code="2330", name="台積電", close=Decimal("1000")),
        StockQuote(code="2408", name="南亞科", close=None),
    ]


def test_load_stock_list_from_firestore_returns_empty_when_doc_missing():
    firestore_client = MagicMock()
    snapshot = MagicMock(exists=False)
    firestore_client.collection.return_value.document.return_value.get.return_value = snapshot

    result = tick._load_stock_list_from_firestore(firestore_client=firestore_client)

    assert result == []


def test_get_cached_stock_list_falls_back_to_firestore_when_memory_empty(monkeypatch):
    stock_list = [StockQuote(code="2330", name="台積電", close=Decimal("1000"))]
    monkeypatch.setattr(tick, "_cached_stock_list", [])
    monkeypatch.setattr(tick, "_load_stock_list_from_firestore", MagicMock(return_value=stock_list))

    result = tick.get_cached_stock_list()

    assert result == stock_list
    assert tick._cached_stock_list == stock_list


def _friend(line_user_id: str) -> FriendRecord:
    return FriendRecord(line_user_id=line_user_id, spreadsheet_id=f"sheet-{line_user_id}", encrypted_refresh_token="enc")


def _http_404() -> HttpError:
    class _FakeResp:
        status = 404
        reason = "Not Found"

    return HttpError(_FakeResp(), b"{}")


def test_run_daily_close_task_resyncs_each_active_friend_with_interval_between_calls():
    firestore_client = MagicMock()
    firestore_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    friends = [_friend("U1"), _friend("U2")]
    stock_list = [StockQuote(code="2330", name="台積電", close=Decimal("1000"))]
    resync_fn = MagicMock()
    sleep_fn = MagicMock()

    tick.run_daily_close_task(
        now=_now(14, 30),
        firestore_client=firestore_client,
        fetch_stock_list_fn=MagicMock(return_value=stock_list),
        save_stock_list_fn=MagicMock(),
        list_friends_fn=MagicMock(return_value=friends),
        resync_fn=resync_fn,
        sleep_fn=sleep_fn,
    )

    assert resync_fn.call_count == 2
    resync_fn.assert_any_call(friends[0], stock_list, firestore_client=firestore_client)
    resync_fn.assert_any_call(friends[1], stock_list, firestore_client=firestore_client)
    sleep_fn.assert_called_once_with(tick.FRIEND_RESYNC_INTERVAL_SECONDS)


def test_run_daily_close_task_continues_after_oauth_invalid_grant():
    firestore_client = MagicMock()
    firestore_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    friends = [_friend("U1"), _friend("U2")]
    resync_fn = MagicMock(side_effect=[OAuthInvalidGrantError("boom"), None])

    tick.run_daily_close_task(
        now=_now(14, 30),
        firestore_client=firestore_client,
        fetch_stock_list_fn=MagicMock(return_value=[]),
        save_stock_list_fn=MagicMock(),
        list_friends_fn=MagicMock(return_value=friends),
        resync_fn=resync_fn,
        sleep_fn=MagicMock(),
    )

    assert resync_fn.call_count == 2
    firestore_client.collection.return_value.document.return_value.set.assert_called_once_with(
        {"last_run_date": "2026-06-25"}
    )


def test_run_daily_close_task_continues_after_sheets_404():
    firestore_client = MagicMock()
    firestore_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    friends = [_friend("U1"), _friend("U2")]
    resync_fn = MagicMock(side_effect=[_http_404(), None])

    tick.run_daily_close_task(
        now=_now(14, 30),
        firestore_client=firestore_client,
        fetch_stock_list_fn=MagicMock(return_value=[]),
        save_stock_list_fn=MagicMock(),
        list_friends_fn=MagicMock(return_value=friends),
        resync_fn=resync_fn,
        sleep_fn=MagicMock(),
    )

    assert resync_fn.call_count == 2
    firestore_client.collection.return_value.document.return_value.set.assert_called_once_with(
        {"last_run_date": "2026-06-25"}
    )


def test_run_daily_close_task_propagates_unexpected_error_and_skips_marking_executed():
    firestore_client = MagicMock()
    firestore_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    friends = [_friend("U1")]
    resync_fn = MagicMock(side_effect=ValueError("boom"))

    with pytest.raises(ValueError):
        tick.run_daily_close_task(
            now=_now(14, 30),
            firestore_client=firestore_client,
            fetch_stock_list_fn=MagicMock(return_value=[]),
            save_stock_list_fn=MagicMock(),
            list_friends_fn=MagicMock(return_value=friends),
            resync_fn=resync_fn,
            sleep_fn=MagicMock(),
        )

    firestore_client.collection.return_value.document.return_value.set.assert_not_called()
