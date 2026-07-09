"""Optional ntfy push notifications. Fails soft — notification problems never
block the caller. Config in AppSetting key "notifications"."""
import json
import logging

import httpx

from app.models.app_setting import AppSetting
from app.schemas.settings import NotificationSettings

logger = logging.getLogger("powarr")


def load_settings(db) -> NotificationSettings:
    row = db.query(AppSetting).filter_by(key="notifications").first()
    if not row or not row.value:
        return NotificationSettings()
    return NotificationSettings(**json.loads(row.value))


async def notify(db, title: str, message: str, priority: str = "default", tags: str = "",
                 actions: list[str] | None = None) -> bool:
    """`actions` is a list of ntfy action specs (e.g. "http, Accept, <url>,
    method=GET, clear=true") — joined into the `Actions` header. See
    https://docs.ntfy.sh/publish/#action-buttons. Click-to-act links (v0.26.0)."""
    cfg = load_settings(db)
    if not cfg.enabled or not cfg.ntfy_url or not cfg.topic:
        return False
    url = f"{cfg.ntfy_url.rstrip('/')}/{cfg.topic}"
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if actions:
        headers["Actions"] = "; ".join(actions)
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            r = await client.post(url, content=message.encode(), headers=headers)
            return r.status_code // 100 == 2
    except Exception as e:
        logger.info(f"ntfy notification failed (non-fatal): {e}")
        return False
