"""Optional push notifications — ntfy and Discord webhook (NOTIF-01, v0.76.0),
both fail-soft, config in AppSetting key "notifications". Each channel is
independent: one failing or being unconfigured never blocks the other."""
import json
import logging
import re

import httpx

from app.models.app_setting import AppSetting
from app.schemas.settings import NotificationSettings

logger = logging.getLogger("powarr")


def load_settings(db) -> NotificationSettings:
    row = db.query(AppSetting).filter_by(key="notifications").first()
    if not row or not row.value:
        return NotificationSettings()
    return NotificationSettings(**json.loads(row.value))


_ACTION_SPEC_RE = re.compile(r"^\s*http\s*,\s*([^,]+)\s*,\s*([^,]+)")


def _parse_action_link(spec: str) -> tuple[str, str] | None:
    """Pulls (label, url) out of one ntfy action-spec string (see notify()'s
    docstring) — the shared translation point so the same actions list drives
    both ntfy's native action buttons and a Discord embed's markdown links."""
    m = _ACTION_SPEC_RE.match(spec)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


async def _notify_ntfy(cfg: NotificationSettings, title: str, message: str, priority: str,
                        tags: str, actions: list[str] | None) -> bool:
    if not cfg.ntfy_url or not cfg.topic:
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


async def _notify_discord(cfg: NotificationSettings, title: str, message: str,
                           actions: list[str] | None) -> bool:
    if not cfg.discord_webhook_url:
        return False
    from app.services.secret_box import decrypt
    webhook = decrypt(cfg.discord_webhook_url) or cfg.discord_webhook_url
    if not webhook:
        return False
    description = message
    links = [f"[{label}]({url})" for label, url in
             (p for p in (_parse_action_link(a) for a in (actions or [])) if p)]
    if links:
        description += "\n\n" + " • ".join(links)
    payload = {"username": "Powarr", "embeds": [{"title": title, "description": description}]}
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            r = await client.post(webhook, json=payload)
            return r.status_code // 100 == 2
    except Exception as e:
        logger.info(f"Discord notification failed (non-fatal): {e}")
        return False


async def notify(db, title: str, message: str, priority: str = "default", tags: str = "",
                 actions: list[str] | None = None) -> bool:
    """`actions` is a list of ntfy action specs (e.g. "http, Accept, <url>,
    method=GET, clear=true") — joined into the ntfy `Actions` header, and
    separately parsed into markdown links for a Discord embed. Fans out to
    every enabled channel independently; returns True if at least one channel
    actually sent (click-to-act links, v0.26.0; Discord channel, NOTIF-01)."""
    cfg = load_settings(db)
    ntfy_ok = await _notify_ntfy(cfg, title, message, priority, tags, actions) if cfg.enabled else False
    discord_ok = await _notify_discord(cfg, title, message, actions) if cfg.discord_enabled else False
    return ntfy_ok or discord_ok
