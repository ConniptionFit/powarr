"""LIB-06: library health dashboard. One read-only aggregation of health
signals Powarr already tracks locally — per-type footprint, *arr link
coverage, duplicate-group load, artist-thumbnail coverage (AD-21's cache),
the open failed-import backlog, and how much of the library the protect
flags currently shield. Deliberately computed from the synced local tables
only: no live Plex/*arr calls, so the endpoint is cheap enough to render on
every page visit and can never hang on a flaky integration.

KPIs only, no invented composite "health score" — score formulas are a
confirmation-gated surface in Powarr (see Non-negotiables) and a synthetic
0-100 here would just be an opinion wearing a number."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artist_thumbnail import ArtistThumbnail
from app.models.failed_import import FailedImport
from app.models.malformed_import_flag import MalformedImportFlag
from app.models.media import MediaItem
from app.services.arr_link import ID_FIELD_FOR_MEDIA_TYPE

# FailedImport statuses that still need a human (or the auto-gate) to act.
OPEN_IMPORT_STATUSES = ("suggested", "resolve_failed", "orphan_pending")


def _arr_link_coverage(db: Session) -> list[dict]:
    """Per media_type: how many items carry their *arr link id. Only the three
    types link_arr_ids()/INT-02 actually link (movie/episode/track) — parent
    container rows (show/artist/album) inherit linkage through children and
    would read as false negatives here."""
    out = []
    for media_type, field in ID_FIELD_FOR_MEDIA_TYPE.items():
        col = getattr(MediaItem, field)
        base = (db.query(func.count(MediaItem.id))
                .filter(MediaItem.media_type == media_type,
                        MediaItem.pending_delete_at.is_(None)))
        total = base.scalar() or 0
        linked = base.filter(col.isnot(None)).scalar() or 0
        if total:
            out.append({"media_type": media_type, "arr_field": field,
                        "linked": linked, "total": total})
    return out


def compute_library_health(db: Session) -> dict:
    # Per-type footprint (excludes rows already pending soft-delete purge).
    by_type = [
        {"media_type": mt, "count": count, "total_size_bytes": int(size or 0)}
        for mt, count, size in (
            db.query(MediaItem.media_type, func.count(MediaItem.id),
                     func.sum(MediaItem.file_size))
            .filter(MediaItem.pending_delete_at.is_(None))
            .group_by(MediaItem.media_type)
            .order_by(func.count(MediaItem.id).desc())
            .all()
        )
    ]

    # Duplicate load — reuse LIB-03's grouper wholesale rather than a second
    # opinion on what counts as a duplicate.
    from app.services.duplicate_finder import find_duplicate_groups
    dup_groups = find_duplicate_groups(db)

    # AD-21 thumbnail cache coverage. Counted from the cache table itself (its
    # population is the Lidarr+Plex library union): a NULL image_url row is a
    # confirmed miss — every source was checked and found nothing.
    thumb_total = db.query(func.count(ArtistThumbnail.id)).scalar() or 0
    thumb_with_url = (db.query(func.count(ArtistThumbnail.id))
                      .filter(ArtistThumbnail.image_url.isnot(None)).scalar() or 0)

    open_imports = dict(
        db.query(FailedImport.status, func.count(FailedImport.id))
        .filter(FailedImport.status.in_(OPEN_IMPORT_STATUSES))
        .group_by(FailedImport.status).all()
    )
    malformed_open = (db.query(func.count(MalformedImportFlag.id))
                      .filter(MalformedImportFlag.dismissed.is_(False)).scalar() or 0)

    active = db.query(MediaItem).filter(MediaItem.pending_delete_at.is_(None))
    protections = {
        "seerr_requested": active.filter(MediaItem.protected.is_(True)).count(),
        "recently_watched": active.filter(MediaItem.watch_protected.is_(True)).count(),
        "seeding": active.filter(MediaItem.seeding_protected.is_(True)).count(),
        "in_progress": active.filter(MediaItem.progress_protected.is_(True)).count(),
    }

    return {
        "by_type": by_type,
        "arr_link_coverage": _arr_link_coverage(db),
        "duplicate_groups": len(dup_groups),
        "duplicate_reclaimable_bytes": sum(g["reclaimable_bytes"] for g in dup_groups),
        "artist_thumbnails_total": thumb_total,
        "artist_thumbnails_with_image": thumb_with_url,
        "open_imports_by_status": {s: open_imports.get(s, 0) for s in OPEN_IMPORT_STATUSES},
        "open_imports_total": sum(open_imports.values()),
        "malformed_flags_open": malformed_open,
        "protections": protections,
        "pending_soft_deletes": db.query(func.count(MediaItem.id))
                                  .filter(MediaItem.pending_delete_at.isnot(None)).scalar() or 0,
        "ignored_items": db.query(func.count(MediaItem.id))
                           .filter(MediaItem.ignored.is_(True),
                                   MediaItem.pending_delete_at.is_(None)).scalar() or 0,
    }
