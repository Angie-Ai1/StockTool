from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.schemas import FriendRecord, FriendStatus
from app.routers import oauth_callback


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(oauth_callback.router)
    return TestClient(app)


def _friend() -> FriendRecord:
    return FriendRecord(
        line_user_id="U123456",
        spreadsheet_id="sheet-1",
        encrypted_refresh_token="enc",
        status=FriendStatus.ACTIVE,
    )


# --- 成功流程 -----------------------------------------------------------------------


def test_oauth_callback_success_calls_link_and_pushes_message(client, monkeypatch):
    link_mock = MagicMock(return_value=_friend())
    push_mock = MagicMock()
    monkeypatch.setattr(oauth_callback, "link_friend_account", link_mock)
    monkeypatch.setattr(oauth_callback, "_push_text", push_mock)

    response = client.get("/oauth/callback", params={"state": "U123456", "code": "auth-code"})

    assert response.status_code == 200
    link_mock.assert_called_once_with("U123456", "auth-code")
    push_mock.assert_called_once()
    assert "成功" in response.text


# --- 使用者取消授權 -----------------------------------------------------------------


def test_oauth_callback_google_error_returns_denied_html(client, monkeypatch):
    push_mock = MagicMock()
    monkeypatch.setattr(oauth_callback, "_push_text", push_mock)

    response = client.get(
        "/oauth/callback", params={"state": "U123456", "error": "access_denied"}
    )

    assert response.status_code == 200
    push_mock.assert_called_once_with("U123456", pytest.approx(push_mock.call_args[0][1], abs=0))
    assert "取消" in response.text


def test_oauth_callback_google_error_push_failure_is_silent(client, monkeypatch):
    monkeypatch.setattr(oauth_callback, "_push_text", MagicMock(side_effect=Exception("LINE down")))

    response = client.get(
        "/oauth/callback", params={"state": "U123456", "error": "access_denied"}
    )

    assert response.status_code == 200  # push 失敗不影響回應


# --- 連結流程失敗 -------------------------------------------------------------------


def test_oauth_callback_link_failure_returns_error_html(client, monkeypatch):
    monkeypatch.setattr(
        oauth_callback, "link_friend_account", MagicMock(side_effect=Exception("Sheets error"))
    )
    push_mock = MagicMock()
    monkeypatch.setattr(oauth_callback, "_push_text", push_mock)

    response = client.get("/oauth/callback", params={"state": "U123456", "code": "auth-code"})

    assert response.status_code == 200
    push_mock.assert_called_once()
    assert "失敗" in response.text


def test_oauth_callback_link_failure_push_failure_is_silent(client, monkeypatch):
    monkeypatch.setattr(
        oauth_callback, "link_friend_account", MagicMock(side_effect=Exception("boom"))
    )
    monkeypatch.setattr(oauth_callback, "_push_text", MagicMock(side_effect=Exception("LINE down")))

    response = client.get("/oauth/callback", params={"state": "U123456", "code": "auth-code"})

    assert response.status_code == 200


# --- 缺少必要參數 -------------------------------------------------------------------


def test_oauth_callback_missing_state_returns_422(client):
    response = client.get("/oauth/callback", params={"code": "auth-code"})
    assert response.status_code == 422


def test_oauth_callback_missing_code_and_error_returns_400(client):
    response = client.get("/oauth/callback", params={"state": "U123456"})
    assert response.status_code == 400
