"""LIB-03: duplicate & upgrade hunter. Groups MediaItem rows that look like
the same logical title living as separate Plex library entries — a stale
grab left behind after an upgrade, a re-add, a duplicate import — distinct
from Deletion Suggestions' score-sorted single-item flow (see
[[Scoring System]]). Read-only: this only finds and ranks groups, it never
deletes anything itself. Actual removal goes through the existing
preview-delete / batch-delete endpoints with whichever ids the user picks.

Scoped to the four top-level media types (movie/show/artist/album) — episodes
and tracks are children of those and would duplicate at a different
granularity (same episode number across two different releases) that this
doesn't attempt."""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.media import MediaItem
from app.services.import_matcher import _normalize

TOP_LEVEL_TYPES = ("movie", "show", "artist", "album")


def _group_key(item: MediaItem) -> tuple:
    norm = _normalize(item.title)
    # Movies legitimately have same-title remakes ("The Thing" 1982 vs 2011) —
    # year disambiguates those. Shows/artists/albums rarely carry a dependable
    # per-item year in Plex's own metadata, and a genuine duplicate there is
    # virtually always the same title regardless of any year drift between
    # the two Plex entries, so year is deliberately not part of that key.
    year = item.year if item.media_type == "movie" else None
    return (item.media_type, norm, year)


def find_duplicate_groups(db: Session) -> list[dict]:
    items = (
        db.query(MediaItem)
        .filter(MediaItem.media_type.in_(TOP_LEVEL_TYPES))
        .filter(MediaItem.ignored.is_(False))
        .filter(MediaItem.pending_delete_at.is_(None))
        .all()
    )
    groups: dict[tuple, list[MediaItem]] = defaultdict(list)
    for item in items:
        key = _group_key(item)
        if not key[1]:  # empty-after-normalize title — nothing to group on
            continue
        groups[key].append(item)

    result = []
    for (media_type, _norm_title, year), members in groups.items():
        if len(members) < 2:
            continue
        # `show` and `artist` MediaItem rows (and often `album`) are Plex
        # container entries — file_size/file_path live on their child
        # episodes/tracks, not the parent row itself, so every member here
        # is frequently 0. Sorting still gives a deterministic order, but
        # "largest file" is not a real quality signal when nothing in the
        # group has a nonzero size — has_size_signal tells the caller not to
        # present the top pick as a confident recommendation in that case.
        members.sort(key=lambda m: m.file_size or 0, reverse=True)
        has_size_signal = any((m.file_size or 0) > 0 for m in members)
        reclaimable = sum((m.file_size or 0) for m in members[1:])
        result.append({
            "media_type": media_type,
            "title": members[0].title,
            "year": year,
            "items": members,
            "suggested_keep_id": members[0].id,
            "has_size_signal": has_size_signal,
            "total_size_bytes": sum(m.file_size or 0 for m in members),
            "reclaimable_bytes": reclaimable,
        })
    # Groups with a real size signal and real reclaimable space surface
    # first; zero-signal groups (still genuine duplicates, just without a
    # space number to rank by) sort after, newest-title-alphabetical.
    result.sort(key=lambda g: (not g["has_size_signal"], -g["reclaimable_bytes"], g["title"]))
    return result
