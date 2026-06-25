from unittest.mock import MagicMock

from app.models.schemas import FriendRecord, FriendStatus
from app.services import friend_repository


def test_get_friend_record_returns_none_when_missing():
    fake_client = MagicMock()
    fake_client.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)

    assert friend_repository.get_friend_record("U123456", firestore_client=fake_client) is None


def test_get_friend_record_parses_existing_doc():
    fake_client = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {
        "line_user_id": "U123456",
        "spreadsheet_id": "sheet-1",
        "encrypted_refresh_token": "enc",
        "status": "active",
    }
    fake_client.collection.return_value.document.return_value.get.return_value = snapshot

    friend = friend_repository.get_friend_record("U123456", firestore_client=fake_client)

    assert friend == FriendRecord(
        line_user_id="U123456", spreadsheet_id="sheet-1", encrypted_refresh_token="enc"
    )


def test_reactivate_friend_updates_firestore_status():
    fake_client = MagicMock()
    friend_repository.reactivate_friend("U123456", firestore_client=fake_client)
    fake_client.collection.return_value.document.return_value.update.assert_called_once_with(
        {"status": FriendStatus.ACTIVE.value}
    )


def test_deactivate_friend_updates_firestore_status():
    fake_client = MagicMock()
    friend_repository.deactivate_friend("U123456", firestore_client=fake_client)
    fake_client.collection.return_value.document.return_value.update.assert_called_once_with(
        {"status": FriendStatus.INACTIVE.value}
    )


def test_mark_needs_reauth_updates_firestore_status():
    fake_client = MagicMock()
    friend_repository.mark_needs_reauth("U123456", firestore_client=fake_client)
    fake_client.collection.return_value.document.return_value.update.assert_called_once_with(
        {"status": FriendStatus.NEEDS_REAUTH.value}
    )


def test_update_account_tabs_cache_overwrites_whole_list():
    fake_client = MagicMock()
    friend_repository.update_account_tabs_cache("U123456", ["個人帳", "大寶存股"], firestore_client=fake_client)
    fake_client.collection.assert_called_once_with("friends")
    fake_client.collection.return_value.document.assert_called_once_with("U123456")
    fake_client.collection.return_value.document.return_value.update.assert_called_once_with(
        {"account_tabs_cache": ["個人帳", "大寶存股"]}
    )


def test_list_active_friends_parses_matching_docs():
    fake_client = MagicMock()
    doc1 = MagicMock()
    doc1.to_dict.return_value = {
        "line_user_id": "U1",
        "spreadsheet_id": "sheet-1",
        "encrypted_refresh_token": "enc-1",
        "status": "active",
    }
    doc2 = MagicMock()
    doc2.to_dict.return_value = {
        "line_user_id": "U2",
        "spreadsheet_id": "sheet-2",
        "encrypted_refresh_token": "enc-2",
        "status": "active",
    }
    fake_client.collection.return_value.where.return_value.stream.return_value = [doc1, doc2]

    friends = friend_repository.list_active_friends(firestore_client=fake_client)

    assert [f.line_user_id for f in friends] == ["U1", "U2"]
    fake_client.collection.assert_called_once_with("friends")
    fake_client.collection.return_value.where.assert_called_once()


def test_list_active_friends_returns_empty_list_when_none_match():
    fake_client = MagicMock()
    fake_client.collection.return_value.where.return_value.stream.return_value = []

    assert friend_repository.list_active_friends(firestore_client=fake_client) == []
