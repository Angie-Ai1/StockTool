import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.models.schemas import FriendRecord, FriendStatus
from app.routers import line_webhook

CHANNEL_SECRET = "dummy-channel-secret"

DUMMY_SETTINGS = Settings(
    line_channel_secret=CHANNEL_SECRET,
    line_channel_access_token="dummy-access-token",
)


@pytest.fixture(autouse=True)
def _dummy_settings(monkeypatch):
    monkeypatch.setattr(line_webhook, "get_settings", lambda: DUMMY_SETTINGS)
    line_webhook._recent_event_ids.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(line_webhook.router)
    return TestClient(app)


def _sign(body: str) -> str:
    digest = hmac.new(CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _post(client, events: list[dict]):
    body = json.dumps({"destination": "Uxxxxdest", "events": events})
    return client.post(
        "/line/webhook",
        content=body,
        headers={"X-Line-Signature": _sign(body), "Content-Type": "application/json"},
    )


def _message_event(user_id: str, *, source_type: str = "user", event_id: str = "01MSG") -> dict:
    source = {"type": source_type, "userId": user_id} if source_type == "user" else {
        "type": source_type,
        "groupId": "Ggroup",
    }
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1700000000000,
        "source": source,
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "reply-token-1",
        "message": {"type": "text", "id": "msgid1", "text": "買 2330 1000 500000", "quoteToken": "qt1"},
    }


def _follow_event(user_id: str, event_id: str = "01FOLLOW") -> dict:
    return {
        "type": "follow",
        "mode": "active",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "reply-token-f",
        "follow": {"isUnblocked": False},
    }


def _unfollow_event(user_id: str, event_id: str = "01UNFOLLOW") -> dict:
    return {
        "type": "unfollow",
        "mode": "active",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": False},
    }


def test_invalid_signature_rejected(client):
    body = json.dumps({"destination": "Uxxxxdest", "events": []})
    response = client.post(
        "/line/webhook",
        content=body,
        headers={"X-Line-Signature": "bogus", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_group_message_ignored(client, monkeypatch):
    get_friend = MagicMock()
    monkeypatch.setattr(line_webhook, "get_friend_record", get_friend)
    response = _post(client, [_message_event("Uxxx", source_type="group")])
    assert response.status_code == 200
    get_friend.assert_not_called()


def test_duplicate_event_processed_once(client, monkeypatch):
    get_friend = MagicMock(return_value=None)
    build_url = MagicMock(return_value="https://example.com/oauth")
    monkeypatch.setattr(line_webhook, "get_friend_record", get_friend)
    monkeypatch.setattr(line_webhook, "build_authorization_url", build_url)
    monkeypatch.setattr(line_webhook, "_reply_text", MagicMock())

    event = _message_event("Uxxx", event_id="01DUP")
    _post(client, [event])
    _post(client, [event])

    assert get_friend.call_count == 1


def test_follow_event_reactivates_inactive_friend(client, monkeypatch):
    inactive_friend = FriendRecord(
        line_user_id="Uxxx",
        spreadsheet_id="sheet-1",
        encrypted_refresh_token="enc",
        status=FriendStatus.INACTIVE,
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=inactive_friend))
    reactivate = MagicMock()
    monkeypatch.setattr(line_webhook, "reactivate_friend", reactivate)

    response = _post(client, [_follow_event("Uxxx")])

    assert response.status_code == 200
    reactivate.assert_called_once_with("Uxxx")


def test_follow_event_new_friend_does_not_reactivate(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=None))
    reactivate = MagicMock()
    monkeypatch.setattr(line_webhook, "reactivate_friend", reactivate)

    _post(client, [_follow_event("Uxxx")])

    reactivate.assert_not_called()


def test_unfollow_event_deactivates_existing_friend(client, monkeypatch):
    active_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc"
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=active_friend))
    deactivate = MagicMock()
    monkeypatch.setattr(line_webhook, "deactivate_friend", deactivate)

    _post(client, [_unfollow_event("Uxxx")])

    deactivate.assert_called_once_with("Uxxx")


def test_unfollow_event_unknown_friend_is_noop(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=None))
    deactivate = MagicMock()
    monkeypatch.setattr(line_webhook, "deactivate_friend", deactivate)

    _post(client, [_unfollow_event("Uxxx")])

    deactivate.assert_not_called()


def test_text_message_from_unlinked_friend_replies_with_oauth_link(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=None))
    monkeypatch.setattr(
        line_webhook, "build_authorization_url", MagicMock(return_value="https://example.com/oauth")
    )
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    _post(client, [_message_event("Uxxx")])

    reply.assert_called_once()
    reply_token, text = reply.call_args.args
    assert reply_token == "reply-token-1"
    assert "https://example.com/oauth" in text


def test_text_message_from_linked_friend_does_not_reply(client, monkeypatch):
    linked_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc"
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=linked_friend))
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    _post(client, [_message_event("Uxxx")])

    reply.assert_not_called()
