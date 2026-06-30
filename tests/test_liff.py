from datetime import date as Date
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.models.schemas import (
    AccountResyncResult,
    FriendRecord,
    FriendStatus,
    Position,
    ResyncResult,
    StockQuote,
    TransactionAction,
    TransactionRow,
)
from app.routers import liff
from app.services.oauth_service import OAuthInvalidGrantError


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(liff.router)
    return TestClient(app)


def _friend(status: FriendStatus = FriendStatus.ACTIVE) -> FriendRecord:
    return FriendRecord(
        line_user_id="U123456", spreadsheet_id="sheet-1", encrypted_refresh_token="enc", status=status
    )


# --- verify_liff_id_token(用 httpx.MockTransport 隔離真實 LINE API) ----------------


def test_call_verify_endpoint_returns_payload_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/v2.1/verify"
        return httpx.Response(200, json={"sub": "U123456", "aud": "channel-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = liff._call_verify_endpoint(client, "fake-id-token", "channel-id")

    assert payload["sub"] == "U123456"


def test_call_verify_endpoint_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_description": "IdToken expired"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(liff.InvalidLiffIdTokenError, match="IdToken expired"):
            liff._call_verify_endpoint(client, "fake-id-token", "channel-id")


# --- _extract_bearer_token -----------------------------------------------------------


def test_extract_bearer_token_returns_token():
    assert liff._extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_extract_bearer_token_rejects_non_bearer_header():
    with pytest.raises(HTTPException) as exc_info:
        liff._extract_bearer_token("Basic abc")
    assert exc_info.value.status_code == 401


# --- GET /liff/summary(整合,monkeypatch 掉驗證/Firestore/resync) --------------------


def test_liff_summary_missing_authorization_header_returns_422(client):
    response = client.get("/liff/summary")
    assert response.status_code == 422


def test_liff_summary_rejects_non_bearer_header(client):
    response = client.get("/liff/summary", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


def test_liff_summary_rejects_invalid_id_token(client, monkeypatch):
    monkeypatch.setattr(
        liff, "verify_liff_id_token", MagicMock(side_effect=liff.InvalidLiffIdTokenError("expired"))
    )
    response = client.get("/liff/summary", headers={"Authorization": "Bearer bad-token"})
    assert response.status_code == 401


def test_liff_summary_returns_not_linked_when_no_friend_record(client, monkeypatch):
    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=None))

    response = client.get("/liff/summary", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {"linked": False, "status": None, "accounts": []}


def test_liff_summary_returns_needs_reauth_without_calling_resync(client, monkeypatch):
    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend(FriendStatus.NEEDS_REAUTH)))
    resync_mock = MagicMock()
    monkeypatch.setattr(liff, "resync", resync_mock)

    response = client.get("/liff/summary", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {"linked": True, "status": "needs_reauth", "accounts": []}
    resync_mock.assert_not_called()


def test_liff_summary_builds_account_summaries_with_unrealized_pnl(client, monkeypatch):
    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend()))
    stock_list = [StockQuote(code="2330", name="台積電", close=Decimal("700"))]
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=stock_list))
    resync_result = ResyncResult(
        accounts=[
            AccountResyncResult(
                tab_name="個人帳",
                positions=[
                    Position(stock_code="2330", quantity=Decimal("10"), avg_cost=Decimal("600"))
                ],
            )
        ]
    )
    monkeypatch.setattr(liff, "read_all_account_positions", MagicMock(return_value=resync_result))

    response = client.get("/liff/summary", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["linked"] is True
    assert body["status"] == "active"
    position = body["accounts"][0]["positions"][0]
    assert position["stock_name"] == "台積電"
    assert position["closing_price"] == "700"
    assert position["unrealized_pnl"] == "1000"


def test_liff_summary_falls_back_to_needs_reauth_on_oauth_invalid_grant(client, monkeypatch):
    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend()))
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=[]))
    monkeypatch.setattr(
        liff, "read_all_account_positions", MagicMock(side_effect=OAuthInvalidGrantError("boom"))
    )

    response = client.get("/liff/summary", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {"linked": True, "status": "needs_reauth", "accounts": []}


def test_liff_summary_falls_back_to_needs_reauth_on_sheets_404(client, monkeypatch):
    class _FakeResp:
        status = 404
        reason = "Not Found"

    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend()))
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=[]))
    monkeypatch.setattr(
        liff, "read_all_account_positions", MagicMock(side_effect=HttpError(_FakeResp(), b"{}"))
    )

    response = client.get("/liff/summary", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {"linked": True, "status": "needs_reauth", "accounts": []}


# --- POST /sheets/sync -----------------------------------------------------------


def test_sheets_sync_returns_ok_for_valid_spreadsheet(client, monkeypatch):
    monkeypatch.setattr(liff, "get_friend_by_spreadsheet_id", MagicMock(return_value=_friend()))
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=[]))
    monkeypatch.setattr(liff, "resync", MagicMock(return_value=MagicMock()))

    response = client.post("/sheets/sync", json={"spreadsheet_id": "sheet-1"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sheets_sync_returns_404_for_unknown_spreadsheet(client, monkeypatch):
    monkeypatch.setattr(liff, "get_friend_by_spreadsheet_id", MagicMock(return_value=None))

    response = client.post("/sheets/sync", json={"spreadsheet_id": "no-such-sheet"})

    assert response.status_code == 404


def test_sheets_sync_returns_401_for_needs_reauth_friend(client, monkeypatch):
    monkeypatch.setattr(
        liff, "get_friend_by_spreadsheet_id", MagicMock(return_value=_friend(FriendStatus.NEEDS_REAUTH))
    )

    response = client.post("/sheets/sync", json={"spreadsheet_id": "sheet-1"})

    assert response.status_code == 401


def test_sheets_sync_returns_401_on_oauth_invalid_grant(client, monkeypatch):
    monkeypatch.setattr(liff, "get_friend_by_spreadsheet_id", MagicMock(return_value=_friend()))
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=[]))
    monkeypatch.setattr(liff, "resync", MagicMock(side_effect=OAuthInvalidGrantError("boom")))

    response = client.post("/sheets/sync", json={"spreadsheet_id": "sheet-1"})

    assert response.status_code == 401


# --- GET /liff/dashboard-data（合併端點：驗一次、讀一次，同時回 summary + history）---


def test_dashboard_data_returns_summary_and_history_with_single_verify(client, monkeypatch):
    verify = MagicMock(return_value="U123456")
    monkeypatch.setattr(liff, "verify_liff_id_token", verify)
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend()))
    stock_list = [StockQuote(code="2330", name="台積電", close=Decimal("700"))]
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=stock_list))

    resync_result = ResyncResult(
        accounts=[
            AccountResyncResult(
                tab_name="個人帳",
                positions=[
                    Position(stock_code="2330", quantity=Decimal("10"), avg_cost=Decimal("600"))
                ],
            )
        ]
    )
    transactions_by_tab = {
        "個人帳": [
            TransactionRow(
                row_uuid="u1",
                date=Date(2026, 6, 1),
                action=TransactionAction.BUY,
                stock_query="2330 台積電",
                quantity=Decimal("10"),
                amount=Decimal("6000"),
            )
        ]
    }
    monkeypatch.setattr(
        liff,
        "read_all_account_data",
        MagicMock(return_value=(resync_result, transactions_by_tab)),
    )

    response = client.get("/liff/dashboard-data", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["linked"] is True
    assert body["status"] == "active"
    # summary 區塊
    assert body["accounts"][0]["positions"][0]["stock_name"] == "台積電"
    # history 區塊(同一次回應)
    assert body["history"] is not None
    assert body["history"]["accounts"][0]["tab_name"] == "個人帳"
    # 合併端點的核心效益：整個流程只驗一次 id_token(原本 summary+history 各驗一次)
    verify.assert_called_once()


def test_dashboard_data_falls_back_to_needs_reauth_on_oauth_invalid_grant(client, monkeypatch):
    monkeypatch.setattr(liff, "verify_liff_id_token", MagicMock(return_value="U123456"))
    monkeypatch.setattr(liff, "get_friend_record", MagicMock(return_value=_friend()))
    monkeypatch.setattr(liff, "get_cached_stock_list", MagicMock(return_value=[]))
    monkeypatch.setattr(
        liff, "read_all_account_data", MagicMock(side_effect=OAuthInvalidGrantError("boom"))
    )

    response = client.get("/liff/dashboard-data", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {
        "linked": True,
        "status": "needs_reauth",
        "accounts": [],
        "history": None,
    }
