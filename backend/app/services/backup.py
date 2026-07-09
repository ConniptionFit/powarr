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
