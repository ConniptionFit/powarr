from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class MediaItemOut(BaseModel):
    id: int
    plex_rating_key: str
    title: str
    year: Optional[int]
    media_type: str
    library_section: Optional[str]
    file_path: Optional[str]
    file_size: int
    added_at: Optional[datetime]
    release_date: Optional[datetime]
    last_watched_at: Optional[datetime]
    watch_count: int
    score: float
    ignored: bool
    parent_title: Optional[str]
    protected: Optional[bool] = False
    watch_protected: Optional[bool] = False
    seeding_protected: Optional[bool] = False
    progress_protected: Optional[bool] = False
    pending_delete_at: Optional[datetime] = None
    llm_rationale: Optional[str] = None
    llm_rationale_at: Optional[datetime] = None
    llm_second_opinion: Optional[str] = None
    llm_second_opinion_at: Optional[datetime] = None
    risky_delete: Optional[bool] = False
    sonarr_id: Optional[int]
    radarr_id: Optional[int]
    lidarr_id: Optional[int]

    model_config = {"from_attributes": True}


class MediaStats(BaseModel):
    total_items: int
    total_size_bytes: int
    candidates_above_threshold: int
    potential_savings_bytes: int
    last_synced: Optional[datetime]


class DeletionLogOut(BaseModel):
    id: int
    title: str
    parent_title: Optional[str] = None
    media_type: str
    library_section: Optional[str] = None
    file_size: int
    arr_action: Optional[str] = None
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DeletionStats(BaseModel):
    deleted_30d: int = 0
    freed_30d_bytes: int = 0
    deleted_total: int = 0
    freed_total_bytes: int = 0


class DeletionPreviewItem(BaseModel):
    """LIB-05 note applies here too: `arr_action`/`cascade_warning` are computed
    the same way propagate_and_delete() would decide them, so the preview can
    never promise something the real delete wouldn't do."""
    id: int
    title: str
    media_type: str
    library_section: Optional[str] = None
    file_size: int
    protected: bool = False
    watch_protected: bool = False
    seeding_protected: bool = False
    progress_protected: bool = False
    arr_app: Optional[str] = None  # sonarr | radarr | lidarr | None (no link)
    arr_action: str = "none"  # delete_from_arr | unmonitor | none
    # Set when this item's *arr action targets a whole series/artist/movie and
    # other MediaItems of that same *arr id exist outside this delete selection
    # (LIB-01 — the "episode vs series" ambiguity flagged for LIB-02).
    cascade_warning: Optional[str] = None


class DeletionPreview(BaseModel):
    items: list[DeletionPreviewItem]
    total_items: int
    missing_count: int  # ids that no longer resolve to a MediaItem
    total_size_bytes: int
    soft_delete_days: int
    would_pend: bool  # true when this delete would soft-delete (pending window) rather than delete immediately
    protected_count: int  # items in this selection currently protected by any flag (Seerr/Tautulli/seeding)


class DuplicateGroupItem(BaseModel):
    """LIB-03 — one MediaItem row within a duplicate group."""
    id: int
    plex_rating_key: str
    title: str
    library_section: Optional[str] = None
    file_path: Optional[str] = None
    file_size: int = 0
    added_at: Optional[datetime] = None
    score: float = 0.0

    model_config = {"from_attributes": True}


class DuplicateGroup(BaseModel):
    """LIB-03 — a set of MediaItem rows that look like the same logical title
    living as separate Plex library entries. `suggested_keep_id` defaults to
    the largest file in the group (Powarr's only quality proxy without an
    extra *arr lookup); the user picks what actually gets deleted.
    `has_size_signal` is False for container types (show/artist, often
    album) whose file_size/file_path live on child episodes/tracks rather
    than the parent row — every member is 0 there, so `suggested_keep_id`
    is just a deterministic tie-break, not a real recommendation."""
    media_type: str
    title: str
    year: Optional[int] = None
    items: list[DuplicateGroupItem]
    suggested_keep_id: int
    has_size_signal: bool = True
    total_size_bytes: int
    reclaimable_bytes: int


class MediaTypeFootprint(BaseModel):
    """LIB-06 — one media_type's count/size within the synced library."""
    media_type: str
    count: int
    total_size_bytes: int


class ArrLinkCoverage(BaseModel):
    """LIB-06 — how many items of a linkable media_type carry their *arr id.
    Only movie/episode/track appear (the types link_arr_ids() actually links —
    parent container rows inherit linkage through children)."""
    media_type: str
    arr_field: str
    linked: int
    total: int


class LibraryHealth(BaseModel):
    """LIB-06 — read-only KPI aggregation computed purely from the synced
    local tables (no live Plex/*arr calls). No composite score on purpose:
    scoring formulas are a confirmation-gated surface in Powarr."""
    by_type: list[MediaTypeFootprint]
    arr_link_coverage: list[ArrLinkCoverage]
    duplicate_groups: int
    duplicate_reclaimable_bytes: int
    artist_thumbnails_total: int
    artist_thumbnails_with_image: int
    open_imports_by_status: dict[str, int]
    open_imports_total: int
    malformed_flags_open: int
    protections: dict[str, int]
    pending_soft_deletes: int
    ignored_items: int
