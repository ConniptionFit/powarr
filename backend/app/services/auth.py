"""Authentication: password login, optional TOTP 2FA, LAN bypass.

All stdlib — PBKDF2 password hashing, HMAC-signed stateless session tokens,
RFC 6238 TOTP (same approach as the old Node Powarr's totp.js).
Disabled by default; LAN bypass on by default so enabling it can't lock out
local users. Config lives in the AppSetting key "auth" — the hash, TOTP secret,
and session secret never leave the backend (auth API returns safe fields only)."""
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import struct
import time
from typing import Any, Optional

from app.models.app_setting import AppSetting

logger = logging.getLogger("powarr")

DEFAULT_LAN_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128"]
SESSION_TTL = 7 * 86400
COOKIE_NAME = "powarr_session"

# Simple in-memory brute-force damper: ip -> (fail_count, locked_until)
_failures: dict[str, tuple[int, float]] = {}
_CACHE: dict[str, Any] = {"cfg": None, "at": 0.0}
_CACHE_TTL = 10


def default_config() -> dict:
    return {
        "enabled": False,
        "username": "",
        "password_hash": "",
        "totp_enabled": False,
        "totp_secret": "",
        "totp_pending_secret": "",
        "lan_bypass": True,
        "lan_cidrs": DEFAULT_LAN_CIDRS,
        "session_secret": secrets.token_hex(32),
        # --- Authentik/forward-auth SSO (v0.24.0) ---
        "sso_enabled": False,
        # Peer IPs/CIDRs allowed to assert identity (the reverse proxy, e.g. NPM on
        # the shared docker network). The identity header is trusted ONLY from these.
        "sso_trusted_proxies": [],
        "sso_username_header": "X-Authentik-Username",
        # When SSO is on, direct (non-proxy) LAN requests are gated by default; this
        # opt-in re-enables the LAN-CIDR bypass for them (Settings → Security → SSO).
        "sso_allow_lan_without_sso": False,
    }


def load_config(db) -> dict:
    row = db.query(AppSetting).filter_by(key="auth").first()
    if not row or not row.value:
        return default_config()
    cfg = default_config() | json.loads(row.value)
    return cfg


def save_config(db, cfg: dict) -> None:
    row = db.query(AppSetting).filter_by(key="auth").first()
    if not row:
        row = AppSetting(key="auth")
        db.add(row)
    row.value = json.dumps(cfg)
    db.commit()
    _CACHE["cfg"] = None  # bust the middleware cache immediately


def load_config_cached(db_factory) -> dict:
    """Middleware-path config load with a short TTL cache to avoid a DB hit per request."""
    now = time.time()
    if _CACHE["cfg"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["cfg"]
    db = db_factory()
    try:
        cfg = load_config(db)
    finally:
        db.close()
    _CACHE["cfg"], _CACHE["at"] = cfg, now
    return cfg


# --- Passwords ---

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# --- Session tokens (stateless, HMAC-signed) ---

def make_token(username: str, secret: str, ttl: int = SESSION_TTL) -> str:
    exp = int(time.time()) + ttl
    payload = f"{username}|{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()

def verify_token(token: str, secret: str) -> Optional[str]:
    """Returns the username if the token is valid and unexpired, else None."""
    try:
        payload_user, payload_exp, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
        payload = f"{payload_user}|{payload_exp}"
        expect = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if int(payload_exp) < time.time():
            return None
        return payload_user
    except Exception:
        return None


# --- TOTP (RFC 6238, SHA-1, 30s step, 6 digits) ---

def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")

def totp_code(secret: str, at: Optional[float] = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)

def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + i * 30), code)
               for i in range(-window, window + 1))

def otpauth_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/Powarr:{username or 'user'}?secret={secret}&issuer=Powarr"


# --- Request evaluation ---

def client_ip(request, cfg: dict | None = None) -> str:
    """Real client IP for CIDR/lockout purposes. X-Forwarded-For is honored only
    from a trusted source:
    - With SSO enabled, ONLY when the direct peer is a configured trusted proxy
      (`sso_trusted_proxies`) — a private peer is no longer enough, which closes
      the leftmost-XFF spoof (SEC-02).
    - Otherwise (SSO off), the legacy behavior: trusted from any private peer."""
    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return peer
    if cfg and cfg.get("sso_enabled"):
        if ip_in_cidrs(peer, cfg.get("sso_trusted_proxies") or []):
            return xff.split(",")[0].strip()
        return peer
    if _is_private(peer):
        return xff.split(",")[0].strip()
    return peer

def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False

def ip_in_cidrs(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False

def sso_identity(request, cfg: dict) -> Optional[str]:
    """The SSO-asserted username, but ONLY when the request's direct peer is a
    configured trusted proxy (`sso_trusted_proxies`). A direct/LAN client is never
    trusted to set the identity header, so it can't forge `X-Authentik-Username`.
    Returns the username, or None when SSO is off / the peer isn't trusted / no
    header is present."""
    if not cfg.get("sso_enabled"):
        return None
    peer = request.client.host if request.client else ""
    if not ip_in_cidrs(peer, cfg.get("sso_trusted_proxies") or []):
        return None
    header = (cfg.get("sso_username_header") or "X-Authentik-Username").lower()
    user = (request.headers.get(header) or "").strip()
    return user or None


def evaluate_request(request, cfg: dict) -> dict:
    """→ {authenticated, bypassed, allowed, via}. Auth+SSO both off = always allowed."""
    sso_on = bool(cfg.get("sso_enabled"))
    if not cfg["enabled"] and not sso_on:
        return {"authenticated": False, "bypassed": False, "allowed": True, "via": None}

    # 1. SSO identity from the trusted proxy — the caller is already authenticated
    #    at the edge (Authentik), so trust the asserted username.
    if sso_on and sso_identity(request, cfg):
        return {"authenticated": True, "bypassed": False, "allowed": True, "via": "sso"}

    # 2. LAN-CIDR bypass. Off by default once SSO is on; the operator re-enables it
    #    with sso_allow_lan_without_sso for direct/local access without SSO.
    ip = client_ip(request, cfg)
    if cfg["lan_bypass"] and ip_in_cidrs(ip, cfg["lan_cidrs"]):
        if not sso_on or cfg.get("sso_allow_lan_without_sso"):
            return {"authenticated": False, "bypassed": True, "allowed": True, "via": "lan"}

    # 3. Session cookie (password login; the localhost break-glass path when SSO is on).
    token = request.cookies.get(COOKIE_NAME, "")
    user = verify_token(token, cfg["session_secret"]) if token else None
    ok = user is not None and user == cfg["username"]
    return {"authenticated": ok, "bypassed": False, "allowed": ok, "via": "session" if ok else None}


# --- Brute-force damper ---

def login_locked(ip: str) -> bool:
    count, until = _failures.get(ip, (0, 0.0))
    return count >= 5 and time.time() < until

def record_login_result(ip: str, success: bool) -> None:
    if success:
        _failures.pop(ip, None)
        return
    count, _ = _failures.get(ip, (0, 0.0))
    _failures[ip] = (count + 1, time.time() + 60)
    if count + 1 >= 5:
        logger.warning(f"Auth: {ip} locked out for 60s after {count + 1} failed logins")
