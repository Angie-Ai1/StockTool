"""親友試算表建立與存取 — 規格 2.2、3.1。

`resync()`(重新讀取試算表、模糊比對、重算損益、寫回快取)屬 1.6,尚未實作;
這裡先提供 1.3 OAuth 連結流程需要的「複製範本進親友 Drive」。
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DEFAULT_SPREADSHEET_NAME = "股市記帳"


def copy_template_to_drive(
    credentials: Credentials, template_id: str, file_name: str = DEFAULT_SPREADSHEET_NAME
) -> str:
    """用親友自己的授權,把範本複製到他自己的 Drive,回傳新檔案的 spreadsheet_id——規格 2.2 步驟 3"""
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    copied = drive_service.files().copy(fileId=template_id, body={"name": file_name}).execute()
    return copied["id"]
