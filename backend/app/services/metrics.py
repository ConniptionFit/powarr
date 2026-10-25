"""Prometheus text-format metrics, computed on scrape from cheap aggregate queries."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artist_discovery import DiscoveredArtist
from app.models.deletion_log import DeletionLog
from app.models.failed_import import FailedImport
from app.models.media import MediaItem


def _label(v: str | None) -> str:
    return (v or "unknown").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _gauge(lines: list[str], name: str, help_: str, samples: list[tuple[dict, float]]) -> None:
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} gauge")
    for labels, value in samples:
        lab = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
        lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")


def render_metrics(db: Session) -> str:
    lines: list[str] = []

    items, size = db.query(func.count(MediaItem.id), func.coalesce(func.sum(MediaItem.file_size), 0)).one()
    _gauge(lines, "powarr_media_items", "Media items in the synced library", [({}, items)])
    _gauge(lines, "powarr_media_bytes", "Total bytes of synced media", [({}, int(size))])

    by_type = db.query(MediaItem.media_type, func.count(MediaItem.id)).group_by(MediaItem.media_type).all()
    _gauge(lines, "powarr_media_items_by_type", "Media items by type",
           [({"media_type": t}, n) for t, n in by_type])

    imports = db.query(FailedImport.status, func.count(FailedImport.id)).group_by(FailedImport.status).all()
    _gauge(lines, "powarr_failed_imports", "Failed imports by status",
           [({"status": s}, n) for s, n in imports])

    cands = db.query(DiscoveredArtist.status, func.count(DiscoveredArtist.id)).group_by(DiscoveredArtist.status).all()
    _gauge(lines, "powarr_discovery_candidates", "Artist discovery candidates by status",
           [({"status": s}, n) for s, n in cands])

    deleted, freed = db.query(func.count(DeletionLog.id), func.coalesce(func.sum(DeletionLog.file_size), 0)).one()
    _gauge(lines, "powarr_deletions", "Deletion log entries", [({}, deleted)])
    _gauge(lines, "powarr_deleted_bytes", "Bytes freed by logged deletions", [({}, int(freed))])

    return "\n".join(lines) + "\n"
