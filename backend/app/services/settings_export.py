"""OPS-02: config-as-code settings export/import — a human-readable, git-diffable
JSON snapshot of every AppSetting row (with any credential-shaped field
redacted — see _redact) plus integration connection metadata (name/url/
enabled only), for disaster recovery or standing up a second instance.
Deliberately separate from services/backup.py's full pg_dump/SQLite-copy
backup: a settings snapshot is orders of magnitude smaller, safe to store
outside the container, and reviewable in a diff — a full DB dump is neither.
Fail-soft on the scheduled path only; the on-demand API path raises so a bad
request surfaces immediately instead of silently no-op-ing."""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.app_setting import AppSetting
from app.models.integration import Integration

logger = logging.getLogger("powarr")

# Real finding from live-testing this export against production data: not
# every credential lives in the Integration table (excluded explicitly below)
# — QdrantSettings (AppSetting key "qdrant") stores a raw `api_key` field
# directly in its own JSON blob, bypassing the Integration/SECRET_MASK system
# entirely, and a real API key was confirmed leaking in plaintext through a
# naive "dump every row" export before this redaction pass was added. Rather
# than hardcode "qdrant" as a special case (which only protects against the
# one credential found today), this scrubs by field-NAME pattern across every
# AppSetting value's nested structure — so any future settings class that
# adds a field named like a credential is protected automatically, with no
# export-side allowlist to remember to update.
_SECRET_KEY_RE = re.compile(r"api[-_]?key|password|passwd|secret|token", re.IGNORECASE)
_REDACTED = "***REDACTED***"


def _redact(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k) and v:
                out[k] = _REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _unredact_merge(existing, incoming):
    """Import-side counterpart to _redact: wherever the incoming payload has
    the redaction sentinel, restore the live value already on this instance
    instead of overwriting a real credential with the placeholder string.
    Importing into a fresh instance with no prior value simply drops the
    field (same "must re-enter credentials" contract as Integration secrets)."""
    if isinstance(incoming, dict):
        existing_dict = existing if isinstance(existing, dict) else {}
        out = {}
        for k, v in incoming.items():
            if v == _REDACTED:
                if k in existing_dict:
                    out[k] = existing_dict[k]
                continue
            out[k] = _unredact_merge(existing_dict.get(k), v)
        return out
    if isinstance(incoming, list):
        existing_list = existing if isinstance(existing, list) else []
        return [
            _unredact_merge(existing_list[i] if i < len(existing_list) else None, v)
            for i, v in enumerate(incoming)
        ]
    return incoming


def export_dir() -> Path:
    return Path(settings.data_dir) / "settings-exports"


def safe_export_path(name: str) -> Path:
    """Same CONTROL-04 guard as backup.safe_backup_path — resolve a filename
    under export_dir only; reject path separators, '..', anything that
    escapes the exports root."""
    base = export_dir().resolve()
    if not name or "/" in name or "\\" in name or name in (".", "..") or ".." in name:
        raise ValueError("invalid export name")
    if not name.startswith("powarr-settings-"):
        raise ValueError("export name must start with powarr-settings-")
    target = (base / name).resolve()
    if not str(target).startswith(str(base) + "/") and target != base:
        raise ValueError("export path escapes settings-exports directory")
    return target


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def export_settings(db, powarr_version: str = "") -> dict:
    """Every AppSetting row (key -> parsed JSON value, secret-shaped fields
    redacted — see _redact) plus non-secret Integration connection metadata
    (name/url/enabled only). `powarr_version` is purely informational (the
    caller passes the live app version — see api/v1/settings.py — rather than
    this module hardcoding one to drift)."""
    app_settings = {}
    for row in db.query(AppSetting).all():
        try:
            app_settings[row.key] = _redact(json.loads(row.value)) if row.value else None
        except (ValueError, TypeError):
            app_settings[row.key] = None
    # Deliberately name/url/enabled only — api_key/username/password/
    # extra_config (which can itself carry a secret, e.g. qBittorrent) are
    # never exported. Restoring an instance from this file must never leak
    # or resurrect a live credential; re-entering them is by design.
    integrations = [
        {"name": i.name, "url": i.url, "enabled": bool(i.enabled)}
        for i in db.query(Integration).all()
    ]
    return {
        "powarr_version": powarr_version,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_settings": app_settings,
        "integrations": integrations,
    }


def import_settings(db, payload: dict) -> dict:
    """Upserts every app_settings key (restoring redacted fields from the
    instance's own live value where one exists — see _unredact_merge; a fresh
    instance with no prior value simply drops the field) and integration
    URL/enabled by name. Never touches api_key/username/password/extra_config
    on an existing Integration row — those simply aren't in the payload to
    begin with, so a restored instance still requires re-entering those
    credentials, by design."""
    app_settings = payload.get("app_settings") or {}
    if not isinstance(app_settings, dict):
        raise ValueError("app_settings must be an object")
    imported = 0
    for key, value in app_settings.items():
        row = db.query(AppSetting).filter_by(key=key).first()
        existing_value = None
        if row and row.value:
            try:
                existing_value = json.loads(row.value)
            except (ValueError, TypeError):
                existing_value = None
        merged = _unredact_merge(existing_value, value)
        if not row:
            row = AppSetting(key=key)
            db.add(row)
        row.value = json.dumps(merged)
        imported += 1

    integrations = payload.get("integrations") or []
    if not isinstance(integrations, list):
        raise ValueError("integrations must be a list")
    updated = 0
    for entry in integrations:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name:
            continue
        row = db.query(Integration).filter_by(name=name).first()
        if not row:
            row = Integration(name=name)
            db.add(row)
        row.url = entry.get("url")
        row.enabled = bool(entry.get("enabled", False))
        updated += 1

    db.commit()
    return {"app_settings_imported": imported, "integrations_updated": updated}


def run_settings_export(db, powarr_version: str = "") -> dict:
    """File-based export for the scheduled path (mirrors backup.run_backup's
    shape/return contract) — writes into settings-exports/, one file per run."""
    d = export_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"powarr-settings-{_timestamp()}.json"
    try:
        data = export_settings(db, powarr_version)
        dest.write_text(json.dumps(data, indent=2))
        return {"ok": True, "path": str(dest), "message": f"Settings export written to {dest.name}"}
    except Exception as e:
        logger.error(f"Settings export failed: {e}")
        return {"ok": False, "path": None, "message": f"Settings export failed: {e}"}


def list_settings_exports() -> list[dict]:
    """Existing settings-export files, newest first (same shape as backup.list_backups)."""
    d = export_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("powarr-settings-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files
    ]


def prune_settings_exports(retention_count: int) -> int:
    """Deletes exports beyond the most recent `retention_count`; <= 0 = unlimited."""
    if retention_count <= 0:
        return 0
    d = export_dir()
    if not d.exists():
        return 0
    files = sorted(d.glob("powarr-settings-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = files[retention_count:]
    for f in to_delete:
        f.unlink(missing_ok=True)
    return len(to_delete)
