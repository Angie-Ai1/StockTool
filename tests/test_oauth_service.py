from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError

from app.config import Settings
from app.models.schemas import FriendStatus
from app.services import oauth_service

DUMMY_SETTINGS = Settings(
    google_oauth_client_id="dummy-client-id",
    google_oauth_client_secret="dummy-client-secret",
    google_oauth_redirect_uri="https://example.com/oauth/callback",
    google_sheets_template_id="dummy-template-id",
    encryption_key=Fernet.generate_key().decode(),
)


@pytest.fixture(autouse=True)
def _dummy_settings(monkeypatch):
    monkeypatch.setattr(oauth_service, "get_settings", lambda: DUMMY_SETTINGS)


def test_build_authorization_url_carries_line_user_id_as_state():
    url = oauth_service.build_authorization_url("U123456")
    query = parse_qs(urlparse(url).query)
    assert query["state"] == ["U123456"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert set(query["scope"][0].split()) == set(oauth_service.SCOPES)


def test_encrypt_decrypt_refresh_token_roundtrip():
    encrypted = oauth_service.encrypt_refresh_token("a-refresh-token")
    assert encrypted != "a-refresh-token"
    assert oauth_service.decrypt_refresh_token(encrypted) == "a-refresh-token"


def test_build_credentials_from_encrypted_refresh_token():
    encrypted = oauth_service.encrypt_refresh_token("a-refresh-token")
    credentials = oauth_service.build_credentials_from_encrypted_refresh_token(encrypted)
    assert credentials.refresh_token == "a-refresh-token"
    assert credentials.client_id == "dummy-client-id"
    assert credentials.client_secret == "dummy-client-secret"
    assert credentials.token_uri == oauth_service.GOOGLE_TOKEN_URI


def test_refresh_or_raise_succeeds_silently_when_refresh_works():
    credentials = MagicMock()
    oauth_service.refresh_or_raise(credentials)
    credentials.refresh.assert_called_once()


def test_refresh_or_raise_wraps_refresh_error():
    credentials = MagicMock()
    credentials.refresh.side_effect = RefreshError("invalid_grant")
    with pytest.raises(oauth_service.OAuthInvalidGrantError):
        oauth_service.refresh_or_raise(credentials)


def test_link_friend_account_orchestrates_exchange_copy_and_firestore_write():
    fake_credentials = MagicMock(refresh_token="a-refresh-token")
    fake_exchanger = MagicMock(return_value=fake_credentials)
    fake_copier = MagicMock(return_value="new-spreadsheet-id")
    fake_firestore_client = MagicMock()

    friend = oauth_service.link_friend_account(
        "U123456",
        "auth-code",
        credentials_exchanger=fake_exchanger,
        template_copier=fake_copier,
        firestore_client=fake_firestore_client,
    )

    fake_exchanger.assert_called_once_with("auth-code", None)
    fake_copier.assert_called_once_with(fake_credentials, "dummy-template-id")
    assert friend.line_user_id == "U123456"
    assert friend.spreadsheet_id == "new-spreadsheet-id"
    assert friend.status is FriendStatus.ACTIVE
    assert oauth_service.decrypt_refresh_token(friend.encrypted_refresh_token) == "a-refresh-token"

    fake_firestore_client.collection.assert_called_once_with("friends")
    fake_firestore_client.collection.return_value.document.assert_called_once_with("U123456")
    fake_firestore_client.collection.return_value.document.return_value.set.assert_called_once()
