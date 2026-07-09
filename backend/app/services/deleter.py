"""Shared deletion pipeline: *arr propagation + audit logging + row removal.
Used by the media API (immediate deletes) and the scheduler (soft-delete purge)."""
import json
import logging

from app.models.deletion_log import DeletionLog
from app.models.integration import Integration
from app.models.media import MediaItem

logger = logging.getLogger("powarr")


async def propagate_and_delete(item: MediaItem, db) -> str:
    """Propagate to the owning *arr app per its config, write the audit row,
    delete the MediaItem. Returns the arr action taken. Caller commits."""
    arr_action = "none"

    async def _handle(name: str, arr_id: int, make_client, delete_fn, unmonitor_fn):
        nonlocal arr_action
        row = db.query(Integration).filter_by(name=name, enabled=True).first()
        if not row:
            return
        extra = json.loads(row.extra_config) if row.extra_config else {}
        client = make_client(row)
        if extra.get("delete_from_arr_list"):
            await delete_fn(client, arr_id)
            arr_action = "deleted_from_arr"
        elif extra.get("remove_from_monitored_on_delete", True):
            await unmonitor_fn(client, arr_id)
            arr_action = "unmonitored"

    from app.services.secret_box import decrypt
    if item.radarr_id:
        from app.integrations.radarr import RadarrIntegration
        await _handle("radarr", item.radarr_id,
                      lambda row: RadarrIntegration(row.url, decrypt(row.api_key) or ""),
                      lambda c, i: c.delete_movie(i), lambda c, i: c.unmonitor_movie(i))
    if item.sonarr_id:
        from app.integrations.sonarr import SonarrIntegration
        await _handle("sonarr", item.sonarr_id,
                      lambda row: SonarrIntegration(row.url, decrypt(row.api_key) or ""),
                      lambda c, i: c.delete_series(i), lambda c, i: c.unmonitor_series(i))
    if item.lidarr_id:
        from app.integrations.lidarr import LidarrIntegration
        await _handle("lidarr", item.lidarr_id,
                      lambda row: LidarrIntegration(row.url, decrypt(row.api_key) or ""),
                      lambda c, i: c.delete_artist(i), lambda c, i: c.unmonitor_artist(i))

    db.add(DeletionLog(
        title=item.title,
        parent_title=item.parent_title,
        media_type=item.media_type,
        library_section=item.library_section,
        file_size=item.file_size or 0,
        arr_action=arr_action,
    ))
    db.delete(item)
    return arr_action
