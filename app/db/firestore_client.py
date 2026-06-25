"""Firestore 連線 — technical_spec.md 3.1。

憑證一律透過 GOOGLE_APPLICATION_CREDENTIALS 指向的檔案路徑取得(Application
Default Credentials 自動讀取這個環境變數),本機與 Cloud Run 共用同一套邏輯:
本機由 docker-compose volume 掛入,Cloud Run 由 Secret Manager 掛載為磁碟區
提供(見 Instruction/cloud_setting.md 4.2 節),不需要另外解析 JSON 字串。
"""

from functools import lru_cache

from google.cloud import firestore

from app.config import get_settings


@lru_cache
def get_firestore_client() -> firestore.Client:
    settings = get_settings()
    return firestore.Client(project=settings.firestore_project_id)
