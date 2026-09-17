"""Shared deletion pipeline: *arr propagation + audit logging + row removal.
Used by the media API (immediate deletes) and the scheduler (soft-delete purge)."""
import json
import logging

from app.models.deletion_log import DeletionLog
from app.models.integration import Integration
from app.models.media import MediaItem

logger = logging.getLogger("powarr")

# LIB-02 — explicit Sonarr episode-delete policy modes. Only meaningful for
# media_type="episode" rows with a sonarr_id; every other item type keeps the
# existing extra_config-driven default (_handle below).
EPISODE_DELETE_MODES = ("episode_files", "unmonitor_season", "unmonitor_series", "remove_from_sonarr")


async def _handle_sonarr_episode(item: MediaItem, db, delete_mode: str) -> str:
    """Episode-level Sonarr delete policy (LIB-02). Sonarr has no native
    per-episode delete/unmonitor distinction in the old default path — deleting
    "one episode" there means delete_series()/unmonitor_series() on the WHOLE
    series (see LIB-01's cascade_warning). This gives the user an explicit,
    narrower choice instead.

    remove_from_sonarr removes the entire series (existing delete_series
    behavior). The other three modes delete the specific episode file first —
    matched by MediaItem.file_path against Sonarr's own episode-file list,
    since MediaItem stores no episode-file id — then optionally widen the
    monitoring change (nothing / this season / the whole series)."""
    row = db.query(Integration).filter_by(name="sonarr", enabled=True).first()
    if not row:
        return "none"
    from app.integrations.sonarr import SonarrIntegration
    from app.services.secret_box import decrypt
    client = SonarrIntegration(row.url, decrypt(row.api_key) or "")

    if delete_mode == "remove_from_sonarr":
        await client.delete_series(item.sonarr_id, delete_files=True)
        return "deleted_from_arr"

    season_number = None
    if item.file_path:
        files = await client.get_episode_files(item.sonarr_id)
        match = next((f for f in files if f.get("path") == item.file_path), None)
        if match:
            await client.delete_episode_file(match["id"])
            season_number = match.get("seasonNumber")

    if delete_mode == "unmonitor_season" and season_number is not None:
        await client.set_season_monitored(item.sonarr_id, {season_number}, False)
        return "unmonitored_season"
    if delete_mode == "unmonitor_series":
        await client.unmonitor_series(item.sonarr_id)
        return "unmonitored"
    return "deleted_episode_file"


async def propagate_and_delete(item: MediaItem, db, delete_mode: str | None = None) -> str:
    """Propagate to the owning *arr app per its config, write the audit row,
    delete the MediaItem. Returns the arr action taken. Caller commits.

    delete_mode: one of EPISODE_DELETE_MODES, only applied for a Sonarr
    episode row. Any other value (including None) falls back to the existing
    extra_config-driven default for every media type — unchanged behavior for
    every caller that doesn't opt into an explicit mode."""
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
        if item.media_type == "episode" and delete_mode in EPISODE_DELETE_MODES:
            arr_action = await _handle_sonarr_episode(item, db, delete_mode)
        else:
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
