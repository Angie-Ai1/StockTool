"""LINE webhook 路由 — 規格 1.2:簽章驗證、僅處理 1:1 私訊、事件去重、follow/unfollow。

訊息(message)事件目前只接到「尚未連結試算表」這一條分支(回覆 OAuth 連結引導,
重用 1.3 已測試過的 oauth_service.build_authorization_url())。完整的記帳解析→
模糊比對→損益計算→寫入試算表流程,要等 1.4 最後一項(多帳戶詢問)、1.6(resync
寫入試算表)、1.9(收盤價快取)都到位後才整合,這裡先不做,避免做出接不起來的
半套流程。
"""

import time

from fastapi import APIRouter, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import (
    FollowEvent,
    MessageEvent,
    TextMessageContent,
    UnfollowEvent,
    UserSource,
)

from app.config import get_settings
from app.models.schemas import FriendStatus
from app.services.friend_repository import deactivate_friend, get_friend_record, reactivate_friend
from app.services.oauth_service import build_authorization_url

router = APIRouter()

# 短時間窗口去重(5~10 分鐘),而非永久記錄事件 ID — ADR-015。
# 用 process 內記憶體即可:窗口夠短,LINE 的重試通常發生在幾秒到幾分鐘內。
_DEDUPE_WINDOW_SECONDS = 600
_recent_event_ids: dict[str, float] = {}


def _is_duplicate_event(webhook_event_id: str) -> bool:
    now = time.monotonic()
    expired = [
        eid for eid, seen_at in _recent_event_ids.items() if now - seen_at > _DEDUPE_WINDOW_SECONDS
    ]
    for eid in expired:
        del _recent_event_ids[eid]
    if webhook_event_id in _recent_event_ids:
        return True
    _recent_event_ids[webhook_event_id] = now
    return False


def _reply_text(reply_token: str, text: str) -> None:
    settings = get_settings()
    configuration = Configuration(access_token=settings.line_channel_access_token)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
        )


def _handle_follow_event(event: FollowEvent) -> None:
    line_user_id = event.source.user_id
    friend = get_friend_record(line_user_id)
    if friend is not None and friend.status == FriendStatus.INACTIVE:
        reactivate_friend(line_user_id)


def _handle_unfollow_event(event: UnfollowEvent) -> None:
    line_user_id = event.source.user_id
    if get_friend_record(line_user_id) is not None:
        deactivate_friend(line_user_id)


def _handle_text_message(event: MessageEvent) -> None:
    line_user_id = event.source.user_id
    if get_friend_record(line_user_id) is None:
        url = build_authorization_url(line_user_id)
        _reply_text(event.reply_token, f"還沒有連結你自己的記帳試算表喔,點這裡授權一下:{url}")
    # 已連結的親友:記帳文字解析→寫入流程留給後續整合(1.4 最後一項/1.6/1.9)


@router.post("/line/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(...)) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    settings = get_settings()
    parser = WebhookParser(settings.line_channel_secret)
    try:
        events = parser.parse(body, x_line_signature)
    except InvalidSignatureError as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    for event in events:
        if not isinstance(event.source, UserSource):
            continue  # 群組/聊天室訊息一律忽略,不回應 — 規格 1.2
        if _is_duplicate_event(event.webhook_event_id):
            continue
        if isinstance(event, FollowEvent):
            _handle_follow_event(event)
        elif isinstance(event, UnfollowEvent):
            _handle_unfollow_event(event)
        elif isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            _handle_text_message(event)

    return {"status": "ok"}
