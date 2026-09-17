import base64
from typing import Any
import httpx

from app.integrations.base import BaseIntegration

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"


class SpotifyIntegration(BaseIntegration):
    """AD-18 — Client Credentials flow only (an app-level token via Basic auth
    on Client ID/Secret, never a user OAuth redirect/login). Client ID lives in
    `username`, Client Secret in `api_key` — same username+api_key combo
    precedent as Last.fm, since this doesn't fit the generic URL+single-key
    `IntegrationCard` either. `url` is unused (fixed Spotify endpoints).

    Spotify has scaled back `preview_url` availability for API apps created
    after Nov 2024 — many searches now legitimately return no preview. That's
    treated the same as any other "no preview available" case, not an error;
    this integration never claims a preview exists when Spotify didn't send one."""
    name = "spotify"

    async def _get_token(self) -> str | None:
        if not self.username or not self.api_key:
            return None
        try:
            creds = base64.b64encode(f"{self.username}:{self.api_key}".encode()).decode()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    _TOKEN_URL,
                    headers={"Authorization": f"Basic {creds}",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "client_credentials"},
                )
                r.raise_for_status()
                return (r.json() or {}).get("access_token")
        except Exception:
            return None

    async def test_connection(self) -> dict[str, Any]:
        token = await self._get_token()
        if not token:
            return {"ok": False, "message": "Could not obtain a token — check Client ID/Secret", "version": None}
        return {"ok": True, "message": "Connected", "version": None}

    async def search_preview(self, artist_name: str) -> dict | None:
        """First track result's 30s preview — {preview_url, title}, or None if
        no token, no match, or Spotify sent no preview_url (never raises)."""
        token = await self._get_token()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(_SEARCH_URL, headers={"Authorization": f"Bearer {token}"},
                                     params={"q": artist_name, "type": "track", "limit": 1})
                r.raise_for_status()
                tracks = ((r.json() or {}).get("tracks") or {}).get("items") or []
                if not tracks:
                    return None
                preview_url = tracks[0].get("preview_url")
                if not preview_url:
                    return None
                return {"preview_url": preview_url, "title": tracks[0].get("name")}
        except Exception:
            return None
