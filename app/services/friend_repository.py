"""Firestore `friends/{line_user_id}` 對照表的共用 CRUD — 規格 1.2、1.3、1.6、2.6。

獨立成一個模組,因為 `oauth_service`(OAuth 連結)跟 `sheets_client`(resync 寫入
試算表)都需要讀寫親友狀態——放在任何一邊都會讓兩個模組互相 import,造成循環依賴。
"""

from google.cloud.firestore_v1.base_query import FieldFilter

from app.db.firestore_client import get_firestore_client
from app.models.schemas import FriendRecord, FriendStatus


def get_friend_record(line_user_id: str, firestore_client=None) -> FriendRecord | None:
    """查無此親友回傳 None——line_webhook 判斷是否已連結試算表、resync 讀取憑證用,規格 1.2"""
    client = firestore_client or get_firestore_client()
    snapshot = client.collection("friends").document(line_user_id).get()
    if not snapshot.exists:
        return None
    return FriendRecord.model_validate(snapshot.to_dict())


def reactivate_friend(line_user_id: str, firestore_client=None) -> None:
    """follow 事件:回鍋舊親友狀態改回啟用——規格 1.2"""
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).update(
        {"status": FriendStatus.ACTIVE.value}
    )


def deactivate_friend(line_user_id: str, firestore_client=None) -> None:
    """unfollow 事件:標記該親友已停用,不刪除資料——規格 1.2"""
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).update(
        {"status": FriendStatus.INACTIVE.value}
    )


def mark_needs_reauth(line_user_id: str, firestore_client=None) -> None:
    """標記 Firestore 狀態為需要重新連結——OAuth 失效、Drive/Sheets 404 共用同一套復原流程,規格 2.6"""
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).update(
        {"status": FriendStatus.NEEDS_REAUTH.value}
    )


def update_account_tabs_cache(line_user_id: str, tab_names: list[str], firestore_client=None) -> None:
    """resync 掃描完所有分頁後整批覆寫,不是局部增修——規格 1.6"""
    client = firestore_client or get_firestore_client()
    client.collection("friends").document(line_user_id).update({"account_tabs_cache": tab_names})


def list_active_friends(firestore_client=None) -> list[FriendRecord]:
    """14:30 排程逐位 resync 用,只挑 active 狀態——inactive(已封鎖)/needs_reauth(待重新連結)
    的親友這次先跳過,不會無意義地嘗試一個已知會失敗或不該再服務的帳號,規格 1.9"""
    client = firestore_client or get_firestore_client()
    docs = client.collection("friends").where(filter=FieldFilter("status", "==", FriendStatus.ACTIVE.value)).stream()
    return [FriendRecord.model_validate(doc.to_dict()) for doc in docs]
