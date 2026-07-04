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
    return {
        "enabled": cfg["enabled"],
        "totp_enabled": cfg["totp_enabled"],
        "authenticated": state["authenticated"],
        "bypassed": state["bypassed"],
        "lan_bypass": cfg["lan_bypass"],
        "lan_cidrs": cfg["lan_cidrs"],
        "username": cfg["username"] if _can_manage(request, cfg) else None,
    }


@router.post("/login")
def login(request: Request, response: Response, body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = auth_svc.load_config(db)
    if not cfg["enabled"]:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")
    ip = auth_svc.client_ip(request)
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
