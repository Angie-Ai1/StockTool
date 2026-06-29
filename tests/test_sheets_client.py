from decimal import Decimal
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.models.schemas import FriendRecord, Position, StockQuote, TransactionAction, TransactionRow
from app.services import sheets_client
from app.services.oauth_service import OAuthInvalidGrantError

HEADER_ROW = ["row_uuid", "日期", "動作", "股票代碼/名稱", "數量", "金額", "狀態"]
HEADER_INDEX = {name: i for i, name in enumerate(HEADER_ROW)}

STOCK_LIST = [
    StockQuote(code="2330", name="台積電", close=Decimal("600")),
    StockQuote(code="2317", name="鴻海", close=Decimal("100")),
]


def test_copy_template_to_drive_returns_new_spreadsheet_id():
    fake_credentials = MagicMock()
    fake_drive_service = MagicMock()
    fake_drive_service.files.return_value.copy.return_value.execute.return_value = {
        "id": "new-spreadsheet-id"
    }

    with patch("app.services.sheets_client.build", return_value=fake_drive_service) as build:
        spreadsheet_id = sheets_client.copy_template_to_drive(fake_credentials, "template-id", "我的記帳表")

    assert spreadsheet_id == "new-spreadsheet-id"
    build.assert_called_once_with("drive", "v3", credentials=fake_credentials, cache_discovery=False)
    fake_drive_service.files.return_value.copy.assert_called_once_with(
        fileId="template-id", body={"name": "我的記帳表"}
    )


# --- map_header_columns -----------------------------------------------------------


def test_map_header_columns_returns_index_when_all_required_headers_present():
    header_row = [*HEADER_ROW, "備註"]
    index = sheets_client.map_header_columns(header_row)
    assert index == {name: i for i, name in enumerate(header_row)}


def test_map_header_columns_returns_none_when_missing_required_header():
    header_row = [h for h in HEADER_ROW if h != "狀態"]
    assert sheets_client.map_header_columns(header_row) is None


# --- resync_account_tab(純函式,不打外部 API) ----------------------------------------


def _row(row_uuid="u1", date="2026-06-01", action="買", stock="2330", quantity="", amount="", status=""):
    return [row_uuid, date, action, stock, quantity, amount, status]


def test_resync_account_tab_applies_buy_then_sell():
    rows = [
        _row(action="買", stock="2330", quantity="10", amount="6000"),
        _row(action="賣", stock="2330", quantity="5", amount="3500"),
    ]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)

    assert statuses == ["", ""]
    assert positions["2330"] == Position(
        stock_code="2330", quantity=Decimal("5"), avg_cost=Decimal("600"), realized_pnl=Decimal("500")
    )


def test_resync_account_tab_marks_oversell_and_does_not_apply_it():
    rows = [_row(action="賣", stock="2330", quantity="10", amount="6000")]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)

    assert statuses == [sheets_client.STATUS_OVERSOLD]
    assert "2330" not in positions


def test_resync_account_tab_marks_unrecognized_stock():
    rows = [_row(action="買", stock="不存在的股票", quantity="10", amount="6000")]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)

    assert statuses == [sheets_client.STATUS_INVALID_ROW]
    assert positions == {}


def test_resync_account_tab_marks_invalid_action_keyword():
    rows = [_row(action="存款", stock="2330", quantity="10", amount="6000")]
    _, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)
    assert statuses == [sheets_client.STATUS_INVALID_ROW]


def test_resync_account_tab_marks_invalid_date():
    rows = [_row(date="not-a-date", action="買", stock="2330", quantity="10", amount="6000")]
    _, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)
    assert statuses == [sheets_client.STATUS_INVALID_ROW]


def test_resync_account_tab_marks_invalid_quantity():
    rows = [_row(action="買", stock="2330", quantity="abc", amount="6000")]
    _, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)
    assert statuses == [sheets_client.STATUS_INVALID_ROW]


def test_resync_account_tab_skips_blank_row():
    rows = [_row(row_uuid="", date="", action="", stock="", quantity="", amount="")]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)
    assert statuses == [""]
    assert positions == {}


def test_resync_account_tab_applies_cash_dividend():
    rows = [
        _row(action="買", stock="2330", quantity="10", amount="6000"),
        _row(action="配息", stock="2330", amount="500"),
    ]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)

    assert statuses == ["", ""]
    assert positions["2330"].avg_cost == Decimal("550")
    assert positions["2330"].quantity == Decimal("10")


def test_resync_account_tab_applies_stock_dividend():
    rows = [
        _row(action="買", stock="2330", quantity="10", amount="6000"),
        _row(action="配股", stock="2330", quantity="2"),
    ]
    positions, statuses = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)

    assert statuses == ["", ""]
    assert positions["2330"].quantity == Decimal("12")
    assert positions["2330"].avg_cost == Decimal("500")


def test_resync_account_tab_tracks_multiple_stocks_independently():
    rows = [
        _row(action="買", stock="2330", quantity="10", amount="6000"),
        _row(action="買", stock="2317", quantity="20", amount="2000"),
    ]
    positions, _ = sheets_client.resync_account_tab(rows, HEADER_INDEX, STOCK_LIST)
    assert set(positions) == {"2330", "2317"}


# --- _column_letter ----------------------------------------------------------------


@pytest.mark.parametrize(
    "index,expected", [(0, "A"), (6, "G"), (25, "Z"), (26, "AA"), (27, "AB")]
)
def test_column_letter(index, expected):
    assert sheets_client._column_letter(index) == expected


# --- _write_status_column -----------------------------------------------------------


def test_write_status_column_noop_when_no_statuses():
    fake_service = MagicMock()
    sheets_client._write_status_column(fake_service, "sheet-1", "個人帳", HEADER_INDEX, [])
    fake_service.spreadsheets.assert_not_called()


def test_write_status_column_writes_status_range():
    fake_service = MagicMock()
    sheets_client._write_status_column(
        fake_service, "sheet-1", "個人帳", HEADER_INDEX, ["", sheets_client.STATUS_OVERSOLD]
    )
    fake_service.spreadsheets.return_value.values.return_value.update.assert_called_once_with(
        spreadsheetId="sheet-1",
        range="'個人帳'!G2:G3",
        valueInputOption="RAW",
        body={"values": [[""], [sheets_client.STATUS_OVERSOLD]]},
    )


# --- resync(整合,mock 掉 Sheets API / Firestore) -------------------------------------


def _fake_friend() -> FriendRecord:
    return FriendRecord(line_user_id="U123456", spreadsheet_id="sheet-1", encrypted_refresh_token="enc")


def _fake_service_with_tabs():
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"title": "個人帳"}},
            {"properties": {"title": "操作面板"}},
        ]
    }

    def fake_values_get(spreadsheetId, range):
        mock = MagicMock()
        if range == "'個人帳'":
            mock.execute.return_value = {
                "values": [HEADER_ROW, _row(action="買", stock="2330", quantity="10", amount="6000")]
            }
        else:
            mock.execute.return_value = {"values": [["說明", "立即同步按鈕"]]}
        return mock

    fake_service.spreadsheets.return_value.values.return_value.get.side_effect = fake_values_get
    return fake_service


def test_resync_registers_recognized_tabs_and_ignores_others(monkeypatch):
    update_cache = MagicMock()
    monkeypatch.setattr(sheets_client, "update_account_tabs_cache", update_cache)
    fake_service = _fake_service_with_tabs()

    result = sheets_client.resync(
        _fake_friend(),
        STOCK_LIST,
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
        firestore_client=MagicMock(),
    )

    assert [account.tab_name for account in result.accounts] == ["個人帳"]
    assert result.accounts[0].positions == [
        Position(stock_code="2330", quantity=Decimal("10"), avg_cost=Decimal("600"))
    ]
    fake_service.spreadsheets.return_value.values.return_value.update.assert_called_once()
    update_cache.assert_called_once_with("U123456", ["個人帳"], firestore_client=ANY)


def test_resync_marks_needs_reauth_and_reraises_on_invalid_grant(monkeypatch):
    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    def raising_refresher(credentials):
        raise OAuthInvalidGrantError("boom")

    with pytest.raises(OAuthInvalidGrantError):
        sheets_client.resync(
            _fake_friend(),
            STOCK_LIST,
            credentials_builder=lambda enc: MagicMock(),
            refresher=raising_refresher,
            sheets_service_builder=MagicMock(),
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


def test_resync_marks_needs_reauth_and_reraises_on_spreadsheet_not_found(monkeypatch):
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 404
        reason = "Not Found"

    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.get.return_value.execute.side_effect = HttpError(
        _FakeResp(), b"{}"
    )

    with pytest.raises(HttpError):
        sheets_client.resync(
            _fake_friend(),
            STOCK_LIST,
            credentials_builder=lambda enc: MagicMock(),
            refresher=MagicMock(),
            sheets_service_builder=lambda credentials: fake_service,
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


# --- read_tab_positions -----------------------------------------------------------


def test_read_tab_positions_returns_positions_from_sheet():
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [HEADER_ROW, _row(action="買", stock="2330", quantity="10", amount="6000")]
    }

    positions = sheets_client.read_tab_positions(
        _fake_friend(),
        "個人帳",
        STOCK_LIST,
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
    )

    assert "2330" in positions
    assert positions["2330"].quantity == Decimal("10")
    fake_service.spreadsheets.return_value.values.return_value.get.assert_called_once_with(
        spreadsheetId="sheet-1", range="'個人帳'"
    )


def test_read_tab_positions_returns_empty_on_empty_sheet():
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": []
    }

    positions = sheets_client.read_tab_positions(
        _fake_friend(),
        "個人帳",
        STOCK_LIST,
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
    )

    assert positions == {}


def test_read_tab_positions_marks_needs_reauth_on_oauth_failure(monkeypatch):
    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    with pytest.raises(OAuthInvalidGrantError):
        sheets_client.read_tab_positions(
            _fake_friend(),
            "個人帳",
            STOCK_LIST,
            credentials_builder=lambda enc: MagicMock(),
            refresher=lambda creds: (_ for _ in ()).throw(OAuthInvalidGrantError("boom")),
            sheets_service_builder=MagicMock(),
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


def test_read_tab_positions_marks_needs_reauth_on_404(monkeypatch):
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 404
        reason = "Not Found"

    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = (
        HttpError(_FakeResp(), b"{}")
    )

    with pytest.raises(HttpError):
        sheets_client.read_tab_positions(
            _fake_friend(),
            "個人帳",
            STOCK_LIST,
            credentials_builder=lambda enc: MagicMock(),
            refresher=MagicMock(),
            sheets_service_builder=lambda credentials: fake_service,
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


# --- append_transaction_row -------------------------------------------------------


def _fake_txn():
    from datetime import date
    return TransactionRow(
        row_uuid="test-uuid-1",
        date=date(2026, 6, 26),
        action=TransactionAction.BUY,
        stock_query="2330 台積電",
        quantity=Decimal("10"),
        amount=Decimal("6000"),
    )


def test_append_transaction_row_appends_correct_row():
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [HEADER_ROW]
    }

    sheets_client.append_transaction_row(
        _fake_friend(),
        "個人帳",
        _fake_txn(),
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
    )

    append_call = fake_service.spreadsheets.return_value.values.return_value.append
    append_call.assert_called_once()
    call_kwargs = append_call.call_args.kwargs
    # range 必須從 A2 開始，避免 Sheets API 在只有標題列時把資料插到 row 1
    assert call_kwargs["range"] == "'個人帳'!A2"
    body = call_kwargs["body"]
    row = body["values"][0]
    assert row[0] == "test-uuid-1"   # row_uuid
    assert row[1] == "=DATE(2026,6,26)"    # 日期(formula 確保 Sheets 自動套用日期格式)
    assert row[2] == "買進"           # 動作
    assert row[3] == "2330 台積電"   # 股票代碼/名稱
    assert row[4] == "10"             # 數量
    assert row[5] == "6000"           # 金額
    assert row[6] == ""               # 狀態(空白,由下次 resync 更新)


def test_append_transaction_row_marks_needs_reauth_on_oauth_failure(monkeypatch):
    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    with pytest.raises(OAuthInvalidGrantError):
        sheets_client.append_transaction_row(
            _fake_friend(),
            "個人帳",
            _fake_txn(),
            credentials_builder=lambda enc: MagicMock(),
            refresher=lambda creds: (_ for _ in ()).throw(OAuthInvalidGrantError("boom")),
            sheets_service_builder=MagicMock(),
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


# --- delete_transaction_rows -------------------------------------------------------


def _fake_sheet_values_with_uuids(uuids: list[str]) -> dict:
    """產生含指定 row_uuid 的假 spreadsheet values 回應"""
    rows = [HEADER_ROW]
    for i, uid in enumerate(uuids):
        rows.append([uid, f"2026-06-{i+1:02d}", "買", "2330", "10", "6000", ""])
    return {"values": rows}


def _fake_spreadsheet_meta(tab_name: str = "個人帳", sheet_id: int = 0) -> dict:
    return {
        "sheets": [{"properties": {"title": tab_name, "sheetId": sheet_id}}]
    }


def test_delete_transaction_rows_deletes_matching_rows():
    """找到 row_uuid 對應的列並呼叫 batchUpdate 刪除"""
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.get.return_value.execute.return_value = (
        _fake_spreadsheet_meta("個人帳", sheet_id=42)
    )
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = (
        _fake_sheet_values_with_uuids(["uuid-1", "uuid-2", "uuid-3"])
    )

    deleted = sheets_client.delete_transaction_rows(
        _fake_friend(),
        [("個人帳", "uuid-2")],
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
    )

    assert deleted == 1
    batch_call = fake_service.spreadsheets.return_value.batchUpdate
    batch_call.assert_called_once()
    requests = batch_call.call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    dim_range = requests[0]["deleteDimension"]["range"]
    assert dim_range["sheetId"] == 42
    assert dim_range["startIndex"] == 2  # header=0, uuid-1=1, uuid-2=2


def test_delete_transaction_rows_returns_zero_when_uuid_not_found():
    """UUID 不存在時回傳 0,不呼叫 batchUpdate"""
    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.get.return_value.execute.return_value = (
        _fake_spreadsheet_meta("個人帳", sheet_id=0)
    )
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = (
        _fake_sheet_values_with_uuids(["uuid-1"])
    )

    deleted = sheets_client.delete_transaction_rows(
        _fake_friend(),
        [("個人帳", "uuid-not-exist")],
        credentials_builder=lambda enc: MagicMock(),
        refresher=MagicMock(),
        sheets_service_builder=lambda credentials: fake_service,
    )

    assert deleted == 0
    fake_service.spreadsheets.return_value.batchUpdate.assert_not_called()


def test_delete_transaction_rows_raises_on_oauth_failure(monkeypatch):
    """OAuth 失效時往上拋並標記 needs_reauth"""
    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    with pytest.raises(OAuthInvalidGrantError):
        sheets_client.delete_transaction_rows(
            _fake_friend(),
            [("個人帳", "uuid-1")],
            credentials_builder=lambda enc: MagicMock(),
            refresher=lambda creds: (_ for _ in ()).throw(OAuthInvalidGrantError("boom")),
            sheets_service_builder=MagicMock(),
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)


def test_append_transaction_row_marks_needs_reauth_on_404(monkeypatch):
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 404
        reason = "Not Found"

    mark_needs_reauth = MagicMock()
    monkeypatch.setattr(sheets_client, "mark_needs_reauth", mark_needs_reauth)

    fake_service = MagicMock()
    fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = (
        HttpError(_FakeResp(), b"{}")
    )

    with pytest.raises(HttpError):
        sheets_client.append_transaction_row(
            _fake_friend(),
            "個人帳",
            _fake_txn(),
            credentials_builder=lambda enc: MagicMock(),
            refresher=MagicMock(),
            sheets_service_builder=lambda credentials: fake_service,
            firestore_client=MagicMock(),
        )

    mark_needs_reauth.assert_called_once_with("U123456", firestore_client=ANY)
