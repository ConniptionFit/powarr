from fastapi import APIRouter, Depends, HTTPException, Request, Response, Body
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth as auth_svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _can_manage(request: Request, cfg: dict) -> bool:
    """Config changes allowed when auth is off, when the caller is authenticated,
    or when the caller earns the LAN bypass."""
    state = auth_svc.evaluate_request(request, cfg)
    return (not cfg["enabled"]) or state["authenticated"] or state["bypassed"]


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    state = auth_svc.evaluate_request(request, cfg)
    manage = _can_manage(request, cfg)
    return {
        "enabled": cfg["enabled"],
        "totp_enabled": cfg["totp_enabled"],
        "authenticated": state["authenticated"],
        "bypassed": state["bypassed"],
        "via": state.get("via"),
        "lan_bypass": cfg["lan_bypass"],
        "lan_cidrs": cfg["lan_cidrs"],
        "sso_enabled": cfg.get("sso_enabled", False),
        "sso_allow_lan_without_sso": cfg.get("sso_allow_lan_without_sso", False),
        # Trusted-proxy list + header name are config — exposed only to a manager.
        "sso_trusted_proxies": cfg.get("sso_trusted_proxies", []) if manage else None,
        "sso_username_header": cfg.get("sso_username_header", "") if manage else None,
        "username": cfg["username"] if manage else None,
    }


@router.post("/login")
def login(request: Request, response: Response, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not cfg["enabled"]:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")
    ip = auth_svc.client_ip(request, cfg)
    if auth_svc.login_locked(ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again in a minute")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ok = username == cfg["username"] and auth_svc.verify_password(password, cfg["password_hash"])
    if ok and cfg["totp_enabled"]:
        ok = auth_svc.verify_totp(cfg["totp_secret"], body.get("totp") or "")
    auth_svc.record_login_result(ip, ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid credentials" + (" or TOTP code" if cfg["totp_enabled"] else ""))

    token = auth_svc.make_token(username, cfg["session_secret"])
    response.set_cookie(auth_svc.COOKIE_NAME, token, max_age=auth_svc.SESSION_TTL,
                        httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth_svc.COOKIE_NAME)
    return {"ok": True}


@router.post("/setup")
def setup(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Set credentials and enable authentication."""
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or len(password) < 8:
        raise HTTPException(status_code=400, detail="Username required; password must be at least 8 characters")
    cfg["username"] = username
    cfg["password_hash"] = auth_svc.hash_password(password)
    cfg["enabled"] = True
    auth_svc.save_config(db, cfg)
    return {"ok": True, "enabled": True}


@router.post("/disable")
def disable(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    if cfg["enabled"] and not auth_svc.verify_password(body.get("password") or "", cfg["password_hash"]):
        raise HTTPException(status_code=401, detail="Password incorrect")
    cfg["enabled"] = False
    cfg["totp_enabled"] = False
    auth_svc.save_config(db, cfg)
    return {"ok": True, "enabled": False}


@router.post("/change-password")
def change_password(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    if not auth_svc.verify_password(body.get("current") or "", cfg["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    new = body.get("new") or ""
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    cfg["password_hash"] = auth_svc.hash_password(new)
    auth_svc.save_config(db, cfg)
    return {"ok": True}


@router.post("/totp/setup")
def totp_setup(request: Request, db: Session = Depends(get_db)):
    """Generate a pending TOTP secret; user confirms with a code to enable."""
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    if not cfg["enabled"]:
        raise HTTPException(status_code=400, detail="Enable authentication first")
    secret = auth_svc.generate_totp_secret()
    cfg["totp_pending_secret"] = secret
    auth_svc.save_config(db, cfg)
    return {"secret": secret, "otpauth_uri": auth_svc.otpauth_uri(secret, cfg["username"])}


@router.post("/totp/enable")
def totp_enable(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    pending = cfg.get("totp_pending_secret", "")
    if not pending:
        raise HTTPException(status_code=400, detail="Run TOTP setup first")
    if not auth_svc.verify_totp(pending, body.get("code") or ""):
        raise HTTPException(status_code=400, detail="Code does not match — check your authenticator app")
    cfg["totp_secret"] = pending
    cfg["totp_pending_secret"] = ""
    cfg["totp_enabled"] = True
    auth_svc.save_config(db, cfg)
    return {"ok": True, "totp_enabled": True}


@router.post("/totp/disable")
def totp_disable(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    if not auth_svc.verify_password(body.get("password") or "", cfg["password_hash"]):
        raise HTTPException(status_code=401, detail="Password incorrect")
    cfg["totp_enabled"] = False
    cfg["totp_secret"] = ""
    auth_svc.save_config(db, cfg)
    return {"ok": True, "totp_enabled": False}


@router.put("/config")
def update_config(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """LAN bypass toggle + CIDR list."""
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")
    if "lan_bypass" in body:
        cfg["lan_bypass"] = bool(body["lan_bypass"])
    if "lan_cidrs" in body and isinstance(body["lan_cidrs"], list):
        cfg["lan_cidrs"] = [c.strip() for c in body["lan_cidrs"] if c.strip()]
    auth_svc.save_config(db, cfg)
    return {"ok": True, "lan_bypass": cfg["lan_bypass"], "lan_cidrs": cfg["lan_cidrs"]}


@router.put("/sso")
def update_sso(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Authentik/forward-auth SSO config: enable, trusted proxy IP/CIDRs, the
    identity header name, and the 'allow LAN without SSO' toggle."""
    cfg = auth_svc.load_config(db)
    if not _can_manage(request, cfg):
        raise HTTPException(status_code=401, detail="Not authorized")

    enabling = bool(body.get("sso_enabled", cfg.get("sso_enabled")))
    allow_lan = bool(body.get("sso_allow_lan_without_sso", cfg.get("sso_allow_lan_without_sso")))
    # Lock-out guard: with SSO on, a direct/localhost request has no way in unless
    # either password login is set (break-glass on localhost) or LAN-without-SSO is
    # allowed. Refuse a config that would strand the operator.
    if enabling and not cfg["enabled"] and not allow_lan:
        raise HTTPException(
            status_code=400,
            detail="Enabling SSO without a password login or 'Allow LAN without SSO' "
                   "would lock you out on localhost — set a password first (Security) "
                   "or enable LAN-without-SSO.")

    if "sso_enabled" in body:
        cfg["sso_enabled"] = enabling
    if "sso_allow_lan_without_sso" in body:
        cfg["sso_allow_lan_without_sso"] = allow_lan
    if "sso_trusted_proxies" in body and isinstance(body["sso_trusted_proxies"], list):
        cfg["sso_trusted_proxies"] = [c.strip() for c in body["sso_trusted_proxies"] if c.strip()]
    if body.get("sso_username_header"):
        cfg["sso_username_header"] = str(body["sso_username_header"]).strip()
    auth_svc.save_config(db, cfg)
    return {"ok": True, "sso_enabled": cfg["sso_enabled"],
            "sso_allow_lan_without_sso": cfg["sso_allow_lan_without_sso"],
            "sso_trusted_proxies": cfg["sso_trusted_proxies"],
            "sso_username_header": cfg["sso_username_header"]}
