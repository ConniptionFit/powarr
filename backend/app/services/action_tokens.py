"""Signed one-time action tokens for ntfy click-to-act notification links
(v0.26.0). HMAC-signed and stateless (payload: import_id|action|expiry|sig) —
no separate "used" bookkeeping needed, because the action itself is the guard:
accept/reject only fire from a "suggested"/"resolve_failed" row, so replaying
an old link against an already-resolved row is a safe no-op (see _accept/
_reject in api/v1/imports.py), never a double-push or a flipped decision."""
import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from app.models.app_setting import AppSetting

# Links embedded in a notification stay clickable for a week — long enough to
# act on a scan alert found days later, short enough that a leaked/old
# notification doesn't grant a standing bypass of the accept/reject gate.
TOKEN_TTL = 7 * 86400

_SECRET_KEY = "notify_action_secret"


def _get_secret(db) -> str:
    row = db.query(AppSetting).filter_by(key=_SECRET_KEY).first()
    if not row or not row.value:
        if not row:
            row = AppSetting(key=_SECRET_KEY)
            db.add(row)
        row.value = secrets.token_hex(32)
        db.commit()
    return row.value


def make_action_token(db, import_id: int, action: str, ttl: int = TOKEN_TTL) -> str:
    secret = _get_secret(db)
    exp = int(time.time()) + ttl
    payload = f"{import_id}|{action}|{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def verify_action_token(db, token: str) -> Optional[tuple[int, str]]:
    """Returns (import_id, action) if the token is well-formed, correctly
    signed, unexpired, and names a known action — else None."""
    try:
        secret = _get_secret(db)
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        id_str, action, exp_str, sig = raw.rsplit("|", 3)
        payload = f"{id_str}|{action}|{exp_str}"
        expect = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if int(exp_str) < time.time():
            return None
        if action not in ("accept", "reject"):
            return None
        return int(id_str), action
    except Exception:
        return None
