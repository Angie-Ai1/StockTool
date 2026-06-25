from unittest.mock import MagicMock, patch

from app.services.sheets_client import copy_template_to_drive


def test_copy_template_to_drive_returns_new_spreadsheet_id():
    fake_credentials = MagicMock()
    fake_drive_service = MagicMock()
    fake_drive_service.files.return_value.copy.return_value.execute.return_value = {
        "id": "new-spreadsheet-id"
    }

    with patch("app.services.sheets_client.build", return_value=fake_drive_service) as build:
        spreadsheet_id = copy_template_to_drive(fake_credentials, "template-id", "我的記帳表")

    assert spreadsheet_id == "new-spreadsheet-id"
    build.assert_called_once_with("drive", "v3", credentials=fake_credentials, cache_discovery=False)
    fake_drive_service.files.return_value.copy.assert_called_once_with(
        fileId="template-id", body={"name": "我的記帳表"}
    )
