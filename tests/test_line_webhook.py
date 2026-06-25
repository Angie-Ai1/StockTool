import base64
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.models.schemas import FriendRecord, FriendStatus, Position, StockQuote
from app.routers import line_webhook
from app.services.oauth_service import OAuthInvalidGrantError

CHANNEL_SECRET = "dummy-channel-secret"

DUMMY_SETTINGS = Settings(
    line_channel_secret=CHANNEL_SECRET,
    line_channel_access_token="dummy-access-token",
)


STOCK_LIST = [StockQuote(code="2330", name="台積電", close=Decimal("600"))]

LINKED_FRIEND = FriendRecord(
    line_user_id="Uxxx",
    spreadsheet_id="sheet-1",
    encrypted_refresh_token="enc",
    account_tabs_cache=["個人"],
)


@pytest.fixture(autouse=True)
def _dummy_settings(monkeypatch):
    monkeypatch.setattr(line_webhook, "get_settings", lambda: DUMMY_SETTINGS)
    line_webhook._recent_event_ids.clear()
    line_webhook._pending_selections.clear()


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


def test_text_message_linked_friend_no_tabs_replies_error(client, monkeypatch):
    """已連結但 account_tabs_cache 為空時,回覆找不到帳戶分頁的提示"""
    friend_no_tabs = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc",
        account_tabs_cache=[],
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=friend_no_tabs))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=[]))
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    _post(client, [_message_event("Uxxx")])

    reply.assert_called_once()
    assert "帳戶分頁" in reply.call_args.args[1]


def test_text_message_needs_reauth_replies_with_reauth_url(client, monkeypatch):
    needs_reauth_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc",
        status=FriendStatus.NEEDS_REAUTH,
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=needs_reauth_friend))
    monkeypatch.setattr(
        line_webhook, "build_authorization_url", MagicMock(return_value="https://example.com/reauth")
    )
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    _post(client, [_message_event("Uxxx")])

    reply.assert_called_once()
    assert "https://example.com/reauth" in reply.call_args.args[1]


def test_text_message_parse_failure_replies_error(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=LINKED_FRIEND))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=[]))
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    event = _message_event("Uxxx")
    event["message"]["text"] = "這不是記帳格式"
    _post(client, [event])

    reply.assert_called_once()
    assert "解析失敗" in reply.call_args.args[1]


def test_text_message_booking_success_single_account(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=LINKED_FRIEND))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=STOCK_LIST))
    monkeypatch.setattr(
        line_webhook, "read_tab_positions", MagicMock(return_value={})
    )
    monkeypatch.setattr(line_webhook, "append_transaction_row", MagicMock())
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    _post(client, [_message_event("Uxxx")])  # 訊息為「買 2330 1000 500000」

    reply.assert_called_once()
    assert "✅" in reply.call_args.args[1]
    assert "2330" in reply.call_args.args[1]
    line_webhook.append_transaction_row.assert_called_once()


def test_text_message_oversell_replies_error(client, monkeypatch):
    existing_position = Position(stock_code="2330", quantity=Decimal("5"))
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=LINKED_FRIEND))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=STOCK_LIST))
    monkeypatch.setattr(
        line_webhook, "read_tab_positions", MagicMock(return_value={"2330": existing_position})
    )
    monkeypatch.setattr(line_webhook, "append_transaction_row", MagicMock())
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    # 嘗試賣出 1000 股,但只有 5 股
    event = _message_event("Uxxx")
    event["message"]["text"] = "賣 2330 1000 500000"
    _post(client, [event])

    reply.assert_called_once()
    assert "庫存不足" in reply.call_args.args[1]
    line_webhook.append_transaction_row.assert_not_called()


def test_text_message_multi_account_no_tag_sends_quick_reply(client, monkeypatch):
    multi_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc",
        account_tabs_cache=["個人", "配偶"],
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=multi_friend))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=STOCK_LIST))
    quick_reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_with_quick_reply", quick_reply)

    _post(client, [_message_event("Uxxx")])

    quick_reply.assert_called_once()
    _, text, options = quick_reply.call_args.args
    assert "帳戶" in text
    assert set(options) == {"個人", "配偶"}
    assert "Uxxx" in line_webhook._pending_selections


def test_text_message_quick_reply_selection_executes_booking(client, monkeypatch):
    multi_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc",
        account_tabs_cache=["個人", "配偶"],
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=multi_friend))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=STOCK_LIST))
    monkeypatch.setattr(line_webhook, "read_tab_positions", MagicMock(return_value={}))
    append = MagicMock()
    monkeypatch.setattr(line_webhook, "append_transaction_row", append)
    reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_text", reply)

    # 先觸發 Quick Reply 詢問
    quick_reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_with_quick_reply", quick_reply)
    _post(client, [_message_event("Uxxx", event_id="01MSG")])
    assert "Uxxx" in line_webhook._pending_selections

    # 使用者點選「個人」
    selection_event = _message_event("Uxxx", event_id="02SEL")
    selection_event["message"]["text"] = "個人"
    _post(client, [selection_event])

    reply.assert_called_once()
    assert "✅" in reply.call_args.args[1]
    append.assert_called_once()
    assert "Uxxx" not in line_webhook._pending_selections


def test_text_message_new_message_cancels_pending_selection(client, monkeypatch):
    multi_friend = FriendRecord(
        line_user_id="Uxxx", spreadsheet_id="sheet-1", encrypted_refresh_token="enc",
        account_tabs_cache=["個人", "配偶"],
    )
    monkeypatch.setattr(line_webhook, "get_friend_record", MagicMock(return_value=multi_friend))
    monkeypatch.setattr(line_webhook, "get_cached_stock_list", MagicMock(return_value=STOCK_LIST))
    quick_reply = MagicMock()
    monkeypatch.setattr(line_webhook, "_reply_with_quick_reply", quick_reply)

    # 先觸發 Quick Reply
    _post(client, [_message_event("Uxxx", event_id="01MSG")])
    assert "Uxxx" in line_webhook._pending_selections

    # 送入不是帳戶名稱的新訊息 → 清除 pending,重新解析
    new_event = _message_event("Uxxx", event_id="02NEW")
    new_event["message"]["text"] = "買 2330 10 6000"
    _post(client, [new_event])

    # pending 應已清除,並再次觸發 Quick Reply(因為還是多帳戶)
    assert quick_reply.call_count == 2
    assert "Uxxx" in line_webhook._pending_selections
