"""Scheduled database backups: pg_dump for Postgres, plain file copy for the
SQLite fallback. Writes into {data_dir}/backups/, one file per run, pruned to
the configured retention count. Fail-soft — a failed backup logs and returns a
message; it never raises into the maintenance loop."""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger("powarr")


def backup_dir() -> Path:
    return Path(settings.data_dir) / "backups"


def safe_backup_path(name: str) -> Path:
    """CONTROL-04 (v0.34.0): resolve a backup filename under backup_dir only.
    Rejects path separators, '..', and anything that escapes the backups root."""
    base = backup_dir().resolve()
    if not name or "/" in name or "\\" in name or name in (".", "..") or ".." in name:
        raise ValueError("invalid backup name")
    if not name.startswith("powarr-"):
        raise ValueError("backup name must start with powarr-")
    target = (base / name).resolve()
    if not str(target).startswith(str(base) + "/") and target != base:
        raise ValueError("backup path escapes backups directory")
    return target


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def run_backup() -> dict:
    """Runs pg_dump (or copies the SQLite file) into the backups dir. Returns
    {"ok": bool, "path": str | None, "message": str}."""
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)

    if settings.is_sqlite:
        src = Path(settings.data_dir) / "powarr.db"
        if not src.exists():
            return {"ok": False, "path": None, "message": "SQLite database file not found"}
        dest = d / f"powarr-{_timestamp()}.db"
        try:
            proc = await asyncio.create_subprocess_exec(
                "cp", str(src), str(dest),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return {"ok": False, "path": None, "message": f"Copy failed: {stderr.decode()[:300]}"}
            return {"ok": True, "path": str(dest), "message": f"SQLite backup written to {dest.name}"}
        except Exception as e:
            return {"ok": False, "path": None, "message": f"Backup failed: {e}"}

    parsed = urlparse(settings.database_url)
    dest = d / f"powarr-{_timestamp()}.sql"
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "powarr",
        "-d", (parsed.path or "/powarr").lstrip("/"),
        "-f", str(dest),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            return {"ok": False, "path": None, "message": f"pg_dump failed: {stderr.decode()[:300]}"}
        return {"ok": True, "path": str(dest), "message": f"Backup written to {dest.name}"}
    except FileNotFoundError:
        return {"ok": False, "path": None, "message": "pg_dump not found in the container image"}
    except Exception as e:
        return {"ok": False, "path": None, "message": f"Backup failed: {e}"}


def list_backups() -> list[dict]:
    """Existing backup files, newest first."""
    d = backup_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("powarr-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files
    ]


def backup_status(enabled: bool, interval_hours: int, last_backup_iso: str | None,
                  now: datetime | None = None) -> dict:
    """OPS-03 — read-only staleness assessment for the scheduled-backup config.
    Reference point is the `last_backup` AppSetting timestamp (written by both
    the scheduler and the on-demand run), falling back to the newest backup
    file's mtime when the setting is missing. A backup counts as stale once its
    age exceeds twice the configured interval — the 2x grace keeps a single
    late maintenance tick from raising a false alarm. Never stale while
    scheduled backups are off: nothing was promised, so nothing is overdue."""
    now = now or datetime.now(timezone.utc)
    files = list_backups()
    newest = files[0] if files else None
    last_dt = None
    if last_backup_iso:
        try:
            last_dt = datetime.fromisoformat(last_backup_iso)
        except ValueError:
            pass
    if last_dt is None and newest:
        last_dt = datetime.fromisoformat(newest["modified"])
    if last_dt is not None and last_dt.tzinfo is None:
        # last_backup is stored as naive UTC (datetime.utcnow().isoformat())
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_hours = (now - last_dt).total_seconds() / 3600 if last_dt else None
    scheduled = enabled and interval_hours > 0
    stale = False
    reason = None
    if scheduled:
        if last_dt is None:
            stale = True
            reason = "Scheduled backups are enabled but none has ever completed"
        elif age_hours > interval_hours * 2:
            stale = True
            reason = f"Last backup was {age_hours:.0f}h ago — expected every {interval_hours}h"
    return {
        "enabled": scheduled,
        "interval_hours": interval_hours,
        "last_backup": last_dt.isoformat() if last_dt else None,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "backup_count": len(files),
        "newest_file": newest["name"] if newest else None,
        "stale": stale,
        "reason": reason,
    }


def prune_backups(retention_count: int) -> int:
    """Deletes backups beyond the most recent `retention_count` (by mtime).
    retention_count <= 0 means unlimited — nothing is pruned. Returns the number
    of files deleted."""
    if retention_count <= 0:
        return 0
    d = backup_dir()
    if not d.exists():
        return 0
    files = sorted(d.glob("powarr-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = files[retention_count:]
    for f in to_delete:
        f.unlink(missing_ok=True)
    return len(to_delete)
