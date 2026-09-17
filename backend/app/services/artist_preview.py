"""AD-18 — listen-before-add preview: resolves an artist name to a YouTube
video ID and/or a Spotify 30s preview clip. Reworked v0.79.0 (user feedback):
the frontend now checks availability when a card scrolls into view and only
shows the Play button when a preview is confirmed, so this is no longer
strictly click-driven — results are cached in-process (24h for confirmed
previews, 1h for misses so a transient failure or quota exhaustion doesn't
hide an artist's preview for a full day) to keep repeat card renders from
re-hitting the YouTube quota (100 units/search, 10k/day default). Each source
is independent and fails soft; a source with no configured+enabled Integration
row is simply omitted from the result rather than erroring. "YouTube Music"
has no official public API and is deliberately not implemented — see Artist
Discovery.md for why."""
import time

from app.models.integration import Integration

_CACHE: dict[tuple, tuple[float, float, dict]] = {}  # key -> (stored_at, ttl, result)
_CACHE_TTL_HIT = 24 * 3600
_CACHE_TTL_MISS = 3600
_CACHE_MAX = 500


def clear_preview_cache() -> None:
    """Test hook + future settings-change invalidation."""
    _CACHE.clear()


async def get_preview(db, artist_name: str) -> dict:
    youtube_row = db.query(Integration).filter_by(name="youtube").first()
    spotify_row = db.query(Integration).filter_by(name="spotify").first()
    yt_on = bool(youtube_row and youtube_row.enabled)
    sp_on = bool(spotify_row and spotify_row.enabled)

    # Enabled-source fingerprint in the key so toggling a source on/off in
    # settings is never masked by a result cached under the old combination.
    key = (artist_name.strip().lower(), yt_on, sp_on)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < hit[1]:
        return hit[2]

    sources: list[dict] = []

    if yt_on:
        from app.api.v1.integrations import _get_client
        result = await _get_client(youtube_row).search_video(artist_name)
        if result:
            sources.append({"source": "youtube", "available": True, **result})
        else:
            sources.append({"source": "youtube", "available": False, "message": "No video found"})

    if sp_on:
        from app.api.v1.integrations import _get_client
        result = await _get_client(spotify_row).search_preview(artist_name)
        if result:
            sources.append({"source": "spotify", "available": True, **result})
        else:
            sources.append({"source": "spotify", "available": False, "message": "No preview available"})

    out = {"sources": sources}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    ttl = _CACHE_TTL_HIT if any(s["available"] for s in sources) else _CACHE_TTL_MISS
    _CACHE[key] = (time.monotonic(), ttl, out)
    return out
