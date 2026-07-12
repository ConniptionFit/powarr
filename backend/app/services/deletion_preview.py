"""LIB-01: non-destructive deletion dry-run / impact preview. Pure (no network
calls, no writes) — the arr_action decision only depends on each *arr
Integration row's extra_config, mirroring services/deleter.py's own decision
exactly so the preview can never promise something the real delete wouldn't do."""
import json

from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.media import DeletionPreview, DeletionPreviewItem
from app.schemas.settings import CleanupSettings

_ARR_SCOPE = {"sonarr": "series", "radarr": "movie", "lidarr": "artist"}


def build_deletion_preview(db, ids: list[int], cleanup: CleanupSettings) -> DeletionPreview:
    id_set = set(ids)
    items = db.query(MediaItem).filter(MediaItem.id.in_(id_set)).all() if id_set else []
    missing_count = len(id_set) - len(items)

    integrations = {
        row.name: row for row in
        db.query(Integration)
        .filter(Integration.name.in_(("sonarr", "radarr", "lidarr")), Integration.enabled.is_(True))
        .all()
    }

    out_items: list[DeletionPreviewItem] = []
    total_size = 0
    protected_count = 0
    for item in items:
        total_size += item.file_size or 0
        seeding_protected = bool(getattr(item, "seeding_protected", False))
        is_protected = bool(item.protected or item.watch_protected or seeding_protected)
        if is_protected:
            protected_count += 1

        arr_app, arr_id, arr_field = None, None, None
        if item.radarr_id:
            arr_app, arr_id, arr_field = "radarr", item.radarr_id, "radarr_id"
        elif item.sonarr_id:
            arr_app, arr_id, arr_field = "sonarr", item.sonarr_id, "sonarr_id"
        elif item.lidarr_id:
            arr_app, arr_id, arr_field = "lidarr", item.lidarr_id, "lidarr_id"

        arr_action = "none"
        cascade_warning = None
        if arr_app:
            row = integrations.get(arr_app)
            if row:
                extra = json.loads(row.extra_config) if row.extra_config else {}
                if extra.get("delete_from_arr_list"):
                    arr_action = "delete_from_arr"
                elif extra.get("remove_from_monitored_on_delete", True):
                    arr_action = "unmonitor"

            if arr_action in ("delete_from_arr", "unmonitor"):
                sibling_count = (
                    db.query(MediaItem)
                    .filter(getattr(MediaItem, arr_field) == arr_id, ~MediaItem.id.in_(id_set))
                    .count()
                )
                if sibling_count > 0:
                    scope = _ARR_SCOPE[arr_app]
                    verb = "delete" if arr_action == "delete_from_arr" else "unmonitor"
                    cascade_warning = (
                        f"Will {verb} the entire {scope} in {arr_app.capitalize()} — "
                        f"{sibling_count} other item(s) of this {scope} in your library "
                        f"are not part of this delete"
                    )

        out_items.append(DeletionPreviewItem(
            id=item.id, title=item.title, media_type=item.media_type,
            library_section=item.library_section, file_size=item.file_size or 0,
            protected=bool(item.protected), watch_protected=bool(item.watch_protected),
            seeding_protected=seeding_protected,
            arr_app=arr_app, arr_action=arr_action, cascade_warning=cascade_warning,
        ))

    return DeletionPreview(
        items=out_items,
        total_items=len(out_items),
        missing_count=missing_count,
        total_size_bytes=total_size,
        soft_delete_days=cleanup.soft_delete_days,
        would_pend=cleanup.soft_delete_days > 0,
        protected_count=protected_count,
    )
