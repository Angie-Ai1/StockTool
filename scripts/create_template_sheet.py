"""一次性腳本：用 service account 建立記帳試算表範本並設定正確欄位結構。

不需要任何 OAuth 流程，直接用 .personal/secrets/firestore-service-account.json 執行。
在專案根目錄執行：poetry run python scripts/create_template_sheet.py
"""

from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

SERVICE_ACCOUNT_FILE = Path(__file__).parent.parent / ".personal" / "secrets" / "firestore-service-account.json"

HEADER_ROW = ["row_uuid", "日期", "動作", "股票代碼/名稱", "數量", "金額", "狀態"]
DEFAULT_TAB_NAME = "個人帳戶"


def get_credentials():
    return service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
    )


def create_template(creds) -> str:
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    # 建立試算表，分頁命名為「個人帳戶」
    spreadsheet = sheets.spreadsheets().create(body={
        "properties": {"title": "【範本】股票記帳"},
        "sheets": [{"properties": {"title": DEFAULT_TAB_NAME}}],
    }).execute()

    spreadsheet_id = spreadsheet["spreadsheetId"]
    print(f"試算表已建立：https://docs.google.com/spreadsheets/d/{spreadsheet_id}")

    # 寫入標題列
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{DEFAULT_TAB_NAME}!A1:G1",
        valueInputOption="RAW",
        body={"values": [HEADER_ROW]},
    ).execute()
    print(f"標題列已寫入：{HEADER_ROW}")

    # 設定「任何知道連結的人可以檢視」（親友複製範本需要讀取權限）
    drive.permissions().create(
        fileId=spreadsheet_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    print("已設定：任何知道連結的人可以檢視")

    return spreadsheet_id


def main() -> None:
    print("使用 service account 建立試算表範本...")
    creds = get_credentials()
    spreadsheet_id = create_template(creds)

    print("\n" + "=" * 60)
    print("完成！請將以下值填入 Cloud Run 環境變數：")
    print(f"\n  GOOGLE_SHEETS_TEMPLATE_ID = {spreadsheet_id}\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
