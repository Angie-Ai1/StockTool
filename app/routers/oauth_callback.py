"""OAuth callback 路由 — 規格 1.3、2.2。

Google 授權完成後重導向到此端點,攜帶 code 與 state(LINE user ID)。
這是 browser redirect,沒有 reply_token,改用 push message 主動通知親友。
"""

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

from app.config import get_settings
from app.services.oauth_service import link_friend_account

router = APIRouter()

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>連結成功</title></head>
<body>
<h2>試算表連結成功！</h2>
<p>請返回 LINE,開始傳記帳訊息吧。</p>
</body>
</html>
"""

_DENIED_HTML = """\
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>授權取消</title></head>
<body>
<h2>授權已取消</h2>
<p>如需使用記帳功能,可在 LINE 重新傳訊息取得授權連結。</p>
</body>
</html>
"""

_ERROR_HTML = """\
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>連結失敗</title></head>
<body>
<h2>連結失敗</h2>
<p>請返回 LINE 重試,或聯絡管理員。</p>
</body>
</html>
"""


def _push_text(line_user_id: str, text: str) -> None:
    settings = get_settings()
    configuration = Configuration(access_token=settings.line_channel_access_token)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=line_user_id, messages=[TextMessage(text=text)])
        )


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    """Google OAuth 重導向進入點。state 為親友的 LINE user ID。"""
    line_user_id = state

    if error:
        try:
            _push_text(line_user_id, "授權已取消。如需使用記帳功能,可再傳訊息給我取得授權連結。")
        except Exception:
            pass
        return HTMLResponse(_DENIED_HTML)

    if not code:
        return HTMLResponse(_ERROR_HTML, status_code=400)

    try:
        link_friend_account(line_user_id, code)
    except Exception:
        logger.exception("link_friend_account failed for user %s", line_user_id)
        try:
            _push_text(line_user_id, "試算表連結失敗,請返回 LINE 重新嘗試授權。")
        except Exception:
            pass
        return HTMLResponse(_ERROR_HTML)

    try:
        _push_text(line_user_id, "試算表連結成功！傳記帳訊息就可以開始記了 📝")
    except Exception:
        pass
    return HTMLResponse(_SUCCESS_HTML)
