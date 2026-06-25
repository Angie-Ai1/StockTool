"""Google OAuth 連結與親友試算表建立 — 規格 2.2、2.3、2.6,技術文件 1.3。

Refresh token 屬於親友本人,後端只負責加密儲存與必要時重新取得 access token,
不持有任何親友的實際財務資料(那些資料留在親友自己的 Drive 試算表裡)。
"""

from collections.abc import Callable

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_settings
from app.db.firestore_client import get_firestore_client
from app.models.schemas import FriendRecord, FriendStatus
from app.services.sheets_client import copy_template_to_drive

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

SCOPES = [
    # 範本擁有者是管理員帳號,親友尚未透過檔案挑選器「開啟」過範本,
    # 較窄的 drive.file 範圍涵蓋不到「複製別人擁有的檔案」這個操作,
    # 因此需要完整 drive 範圍(屬 Google 分類的 restricted scope)——
    # 細節見 openspecs/DE.md。spreadsheets 範圍供之後 resync 讀寫試算表用。
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


class OAuthInvalidGrantError(Exception):
    """親友的 Google 授權已失效(如使用者自行撤銷)— 規格 2.6"""


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def create_oauth_flow() -> Flow:
    settings = get_settings()
    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=settings.google_oauth_redirect_uri
    )


def build_authorization_url(line_user_id: str) -> str:
    """state 帶 LINE user ID,/oauth/callback 收到後對應回這位親友,並防 CSRF——規格 2.2、3.1"""
    flow = create_oauth_flow()
    url, _state = flow.authorization_url(
        access_type="offline", prompt="consent", state=line_user_id
    )
    return url


def exchange_code_for_credentials(code: str) -> Credentials:
    flow = create_oauth_flow()
    flow.fetch_token(code=code)
    return flow.credentials


def encrypt_refresh_token(refresh_token: str) -> str:
    return _fernet().encrypt(refresh_token.encode()).decode()


def decrypt_refresh_token(encrypted_refresh_token: str) -> str:
    return _fernet().decrypt(encrypted_refresh_token.encode()).decode()


def build_credentials_from_encrypted_refresh_token(encrypted_refresh_token: str) -> Credentials:
    settings = get_settings()
    return Credentials(
        token=None,
        refresh_token=decrypt_refresh_token(encrypted_refresh_token),
        token_uri=GOOGLE_TOKEN_URI,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=SCOPES,
    )


def refresh_or_raise(credentials: Credentials) -> None:
    """取得新的 access token;親友撤銷授權時會在這裡撞到 `invalid_grant`——規格 2.6 步驟 1"""
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        raise OAuthInvalidGrantError(str(exc)) from exc


def mark_needs_reauth(line_user_id: str, firestore_client=None) -> None:
    """標記 Firestore 狀態為需要重新連結——OAuth 失效、Drive 404 共用同一套復原流程,規格 2.6"""
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).update(
        {"status": FriendStatus.NEEDS_REAUTH.value}
    )


def link_friend_account(
    line_user_id: str,
    code: str,
    *,
    credentials_exchanger: Callable[[str], Credentials] = exchange_code_for_credentials,
    template_copier: Callable[[Credentials, str], str] = copy_template_to_drive,
    firestore_client=None,
) -> FriendRecord:
    """`/oauth/callback` 收到 code 後的完整連結流程——規格 2.2。

    依序:用授權碼換 token → 用親友自己的授權把範本複製進他的 Drive →
    加密 refresh token → 寫入 Firestore 對照表。
    """
    credentials = credentials_exchanger(code)
    spreadsheet_id = template_copier(credentials, get_settings().google_sheets_template_id)
    friend = FriendRecord(
        line_user_id=line_user_id,
        spreadsheet_id=spreadsheet_id,
        encrypted_refresh_token=encrypt_refresh_token(credentials.refresh_token),
        account_tabs_cache=[],
        status=FriendStatus.ACTIVE,
    )
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).set(friend.model_dump(mode="json"))
    return friend
