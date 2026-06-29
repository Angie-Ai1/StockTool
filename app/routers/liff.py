"""LIFF 網頁資料查詢 API — 規格 1.11、技術文件第 2 章、ADR-009。

身分驗證採 LIFF SDK 的 `id_token`(前端 `liff.getIDToken()` 取得,放在
`Authorization: Bearer <id_token>` header 帶給後端),呼叫 LINE 官方 verify
端點驗證,**絕對不可用網址參數判斷身分**——這是硬性安全要求,即使 MVP 最簡單的
版本也要遵守。

MVP 範圍只回應一個摘要端點:登入連結狀態、目前庫存列表、簡單損益顯示
(master spec 第 7 章)。親友打開 LIFF 網頁時即時呼叫 1.6 `resync()` 重新讀一次
試算表,確保看到的是最新資料,不是排程留下的舊快取。
"""

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app.config import get_settings
from app.models.schemas import (
    AccountSummary,
    FriendStatus,
    LiffSummaryResponse,
    Position,
    PositionSummary,
    StockQuote,
)
from app.routers.tick import get_cached_stock_list
from app.services.friend_repository import get_friend_by_spreadsheet_id, get_friend_record
from app.services.oauth_service import OAuthInvalidGrantError
from app.services.pnl_engine import compute_unrealized_pnl
from app.services.sheets_client import resync

router = APIRouter()

LINE_VERIFY_ID_TOKEN_URL = "https://api.line.me/oauth2/v2.1/verify"


class InvalidLiffIdTokenError(Exception):
    """LIFF id_token 驗證失敗(過期/簽章錯誤/audience 不符)— ADR-009"""


def _call_verify_endpoint(client: httpx.Client, id_token: str, channel_id: str) -> dict:
    response = client.post(
        LINE_VERIFY_ID_TOKEN_URL, data={"id_token": id_token, "client_id": channel_id}
    )
    payload = response.json()
    if response.status_code != 200:
        raise InvalidLiffIdTokenError(payload.get("error_description", "id_token 驗證失敗"))
    return payload


def verify_liff_id_token(id_token: str) -> str:
    """呼叫 LINE 官方 verify 端點,回傳通過驗證的 LINE user ID(`sub`)——規格 ADR-009"""
    settings = get_settings()
    with httpx.Client(timeout=10) as client:
        payload = _call_verify_endpoint(client, id_token, settings.line_login_channel_id)
    return payload["sub"]


def _extract_bearer_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer id_token")
    return authorization.removeprefix("Bearer ").strip()


def _to_position_summary(position: Position, stock: StockQuote | None) -> PositionSummary:
    unrealized_pnl = None
    if stock is not None and stock.close is not None and position.quantity > 0:
        unrealized_pnl = compute_unrealized_pnl(position, stock.close)
    return PositionSummary(
        stock_code=position.stock_code,
        stock_name=stock.name if stock is not None else position.stock_code,
        quantity=position.quantity,
        avg_cost=position.avg_cost,
        realized_pnl=position.realized_pnl,
        closing_price=stock.close if stock is not None else None,
        unrealized_pnl=unrealized_pnl,
    )


@router.get("/oauth/liff", response_class=HTMLResponse)
def oauth_liff_page() -> str:
    """回傳 LIFF 授權頁面，將 __LIFF_ID__ 替換為 settings 中的實際值"""
    with open("app/static/oauth_liff.html") as f:
        html = f.read()
    return html.replace("__LIFF_ID__", get_settings().liff_id)


@router.get("/oauth/url")
def get_oauth_url(authorization: str = Header(...)) -> dict[str, str]:
    """LIFF 頁面用：驗證 id_token 後回傳 Google OAuth URL"""
    id_token = _extract_bearer_token(authorization)
    try:
        line_user_id = verify_liff_id_token(id_token)
    except InvalidLiffIdTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    from app.services.oauth_service import build_authorization_url
    return {"auth_url": build_authorization_url(line_user_id)}


class SyncRequest(BaseModel):
    spreadsheet_id: str


@router.post("/sheets/sync")
def sheets_sync(body: SyncRequest) -> dict[str, str]:
    """由試算表「操作面板」Apps Script 呼叫，以 spreadsheet_id 識別使用者並觸發 resync——規格 1.8"""
    friend = get_friend_by_spreadsheet_id(body.spreadsheet_id)
    if friend is None:
        raise HTTPException(status_code=404, detail="試算表未連結")
    if friend.status == FriendStatus.NEEDS_REAUTH:
        raise HTTPException(status_code=401, detail="需要重新授權")
    try:
        resync(friend, get_cached_stock_list())
    except OAuthInvalidGrantError:
        raise HTTPException(status_code=401, detail="需要重新授權")
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(status_code=404, detail="試算表不存在") from exc
        raise HTTPException(status_code=500, detail="試算表同步失敗") from exc
    return {"status": "ok"}


@router.get("/liff/summary")
def liff_summary(authorization: str = Header(...)) -> LiffSummaryResponse:
    id_token = _extract_bearer_token(authorization)
    try:
        line_user_id = verify_liff_id_token(id_token)
    except InvalidLiffIdTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    friend = get_friend_record(line_user_id)
    if friend is None:
        return LiffSummaryResponse(linked=False)

    if friend.status == FriendStatus.NEEDS_REAUTH:
        return LiffSummaryResponse(linked=True, status=FriendStatus.NEEDS_REAUTH)

    stock_list = get_cached_stock_list()
    try:
        result = resync(friend, stock_list)
    except (OAuthInvalidGrantError, HttpError):
        return LiffSummaryResponse(linked=True, status=FriendStatus.NEEDS_REAUTH)

    stock_by_code = {stock.code: stock for stock in stock_list}
    accounts = [
        AccountSummary(
            tab_name=account.tab_name,
            positions=[
                _to_position_summary(position, stock_by_code.get(position.stock_code))
                for position in account.positions
            ],
        )
        for account in result.accounts
    ]
    return LiffSummaryResponse(linked=True, status=friend.status, accounts=accounts)
