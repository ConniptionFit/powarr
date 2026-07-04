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
    pending_delete_at: Optional[datetime] = None
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
