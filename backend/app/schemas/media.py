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
