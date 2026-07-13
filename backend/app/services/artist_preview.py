"""AD-18 — on-demand listen-before-add preview: resolves an artist name to a
YouTube video ID and/or a Spotify 30s preview clip. Never automatic — only
called when the user clicks Preview on a Discovery/Related Artists card, one
call per click, never bulk. Each source is independent and fails soft; a
source with no configured+enabled Integration row is simply omitted from the
result rather than erroring. "YouTube Music" has no official public API and
is deliberately not implemented — see Artist Discovery.md for why."""
from app.models.integration import Integration


async def get_preview(db, artist_name: str) -> dict:
    sources: list[dict] = []

    youtube_row = db.query(Integration).filter_by(name="youtube").first()
    if youtube_row and youtube_row.enabled:
        from app.api.v1.integrations import _get_client
        result = await _get_client(youtube_row).search_video(artist_name)
        if result:
            sources.append({"source": "youtube", "available": True, **result})
        else:
            sources.append({"source": "youtube", "available": False, "message": "No video found"})

    spotify_row = db.query(Integration).filter_by(name="spotify").first()
    if spotify_row and spotify_row.enabled:
        from app.api.v1.integrations import _get_client
        result = await _get_client(spotify_row).search_preview(artist_name)
        if result:
            sources.append({"source": "spotify", "available": True, **result})
        else:
            sources.append({"source": "spotify", "available": False, "message": "No preview available"})

    return {"sources": sources}
