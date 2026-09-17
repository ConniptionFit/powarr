"""LIB-06: library health dashboard. One read-only aggregation of health
signals Powarr already tracks locally — per-type footprint, *arr link
coverage, duplicate-group load, artist-thumbnail coverage (AD-21's cache),
the open failed-import backlog, and how much of the library the protect
flags currently shield. Deliberately computed from the synced local tables
only: no live Plex/*arr calls, so the endpoint is cheap enough to render on
every page visit and can never hang on a flaky integration.

KPIs only, no invented composite "health score" — score formulas are a
confirmation-gated surface in Powarr (see Non-negotiables) and a synthetic
0-100 here would just be an opinion wearing a number.

PERF (v0.89.0): every media_items KPI here comes from ONE grouped pass using
aggregate FILTER clauses. The previous shape issued ~13 separate counts, each
a full scan of a 160k-row table, on an endpoint whose whole design goal is to
be cheap enough for every page visit — the aggregations were fine, the
repetition was the cost. FILTER is supported by PostgreSQL and by SQLite
>=3.30 (the unit tests run in-memory SQLite), so this needs no dialect split.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artist_thumbnail import ArtistThumbnail
from app.models.failed_import import FailedImport
from app.models.malformed_import_flag import MalformedImportFlag
from app.models.media import MediaItem
from app.services.arr_link import ID_FIELD_FOR_MEDIA_TYPE

# FailedImport statuses that still need a human (or the auto-gate) to act.
OPEN_IMPORT_STATUSES = ("suggested", "resolve_failed", "orphan_pending")


def _media_rollup(db: Session) -> list:
    """One pass over media_items -> every per-type and global KPI below.

    `linked` resolves the right *arr id column per media_type inside the
    aggregate. That is safe precisely because media_type is the GROUP BY key,
    so exactly one arm of the OR can ever apply within a given output row —
    it is the same movie->radarr / episode->sonarr / track->lidarr mapping
    ID_FIELD_FOR_MEDIA_TYPE declares, expressed as SQL. Only those three types
    link at all; parent container rows (show/artist/album) inherit linkage
    through their children and would read as false negatives, so they get a
    NULL-free zero here and are dropped by the caller.
    """
    active = MediaItem.pending_delete_at.is_(None)
    linked = (
        (MediaItem.media_type == "movie") & MediaItem.radarr_id.isnot(None)
        | (MediaItem.media_type == "episode") & MediaItem.sonarr_id.isnot(None)
        | (MediaItem.media_type == "track") & MediaItem.lidarr_id.isnot(None)
    )
    return (
        db.query(
            MediaItem.media_type,
            func.count(MediaItem.id).filter(active).label("active_count"),
            func.coalesce(
                func.sum(MediaItem.file_size).filter(active), 0
            ).label("active_size"),
            func.count(MediaItem.id).filter(active, linked).label("linked"),
            func.count(MediaItem.id)
            .filter(active, MediaItem.protected.is_(True)).label("seerr_requested"),
            func.count(MediaItem.id)
            .filter(active, MediaItem.watch_protected.is_(True)).label("recently_watched"),
            func.count(MediaItem.id)
            .filter(active, MediaItem.seeding_protected.is_(True)).label("seeding"),
            func.count(MediaItem.id)
            .filter(active, MediaItem.progress_protected.is_(True)).label("in_progress"),
            func.count(MediaItem.id)
            .filter(active, MediaItem.ignored.is_(True)).label("ignored_items"),
            func.count(MediaItem.id)
            .filter(MediaItem.pending_delete_at.isnot(None)).label("pending_deletes"),
        )
        .group_by(MediaItem.media_type)
        .all()
    )


def compute_library_health(db: Session) -> dict:
    rollup = _media_rollup(db)

    # Per-type footprint (excludes rows already pending soft-delete purge). A
    # type whose rows are *all* pending-delete contributes nothing and is
    # dropped, matching the old filter-then-group behaviour.
    by_type = sorted(
        (
            {"media_type": r.media_type, "count": r.active_count,
             "total_size_bytes": int(r.active_size or 0)}
            for r in rollup if r.active_count
        ),
        key=lambda d: (-d["count"], d["media_type"]),
    )

    # Iterate the declared mapping, not the rollup: GROUP BY returns rows in
    # whatever order the plan happens to produce, and this list is rendered
    # as-is, so its order has to come from something stable.
    by_mt = {r.media_type: r for r in rollup}
    arr_link_coverage = [
        {"media_type": mt, "arr_field": field,
         "linked": by_mt[mt].linked, "total": by_mt[mt].active_count}
        for mt, field in ID_FIELD_FOR_MEDIA_TYPE.items()
        if mt in by_mt and by_mt[mt].active_count
    ]

    # Duplicate load — reuse LIB-03's grouper wholesale rather than a second
    # opinion on what counts as a duplicate.
    from app.services.duplicate_finder import find_duplicate_groups
    dup_groups = find_duplicate_groups(db)

    # AD-21 thumbnail cache coverage. Counted from the cache table itself (its
    # population is the Lidarr+Plex library union): a NULL image_url row is a
    # confirmed miss — every source was checked and found nothing. One pass,
    # same reasoning as _media_rollup.
    thumb_total, thumb_with_url = db.query(
        func.count(ArtistThumbnail.id),
        func.count(ArtistThumbnail.id).filter(ArtistThumbnail.image_url.isnot(None)),
    ).one()

    open_imports = dict(
        db.query(FailedImport.status, func.count(FailedImport.id))
        .filter(FailedImport.status.in_(OPEN_IMPORT_STATUSES))
        .group_by(FailedImport.status).all()
    )
    malformed_open = (db.query(func.count(MalformedImportFlag.id))
                      .filter(MalformedImportFlag.dismissed.is_(False)).scalar() or 0)

    return {
        "by_type": by_type,
        "arr_link_coverage": arr_link_coverage,
        "duplicate_groups": len(dup_groups),
        "duplicate_reclaimable_bytes": sum(g["reclaimable_bytes"] for g in dup_groups),
        "artist_thumbnails_total": thumb_total or 0,
        "artist_thumbnails_with_image": thumb_with_url or 0,
        "open_imports_by_status": {s: open_imports.get(s, 0) for s in OPEN_IMPORT_STATUSES},
        "open_imports_total": sum(open_imports.values()),
        "malformed_flags_open": malformed_open,
        "protections": {
            "seerr_requested": sum(r.seerr_requested for r in rollup),
            "recently_watched": sum(r.recently_watched for r in rollup),
            "seeding": sum(r.seeding for r in rollup),
            "in_progress": sum(r.in_progress for r in rollup),
        },
        "pending_soft_deletes": sum(r.pending_deletes for r in rollup),
        "ignored_items": sum(r.ignored_items for r in rollup),
    }
