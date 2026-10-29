from typing import Any

import httpx

from app.integrations.base import BaseIntegration

_API = "https://ws.audioscrobbler.com/2.0/"


class LastFmIntegration(BaseIntegration):
    """Read-only Last.fm client for Artist Discovery scrobble ingestion + related-artist
    graph expansion. Auth is a query-param api_key (no signature needed for the public
    GET methods used here) — `username` carries the scrobbling user (BaseIntegration's
    existing username field, same one qBittorrent uses for its own auth)."""

    name = "lastfm"

    def _headers(self) -> dict[str, str]:
        return {}

    def _params(self, method: str, **extra: Any) -> dict[str, Any]:
        params = {"method": method, "api_key": self.api_key, "format": "json", **extra}
        return {k: v for k, v in params.items() if v is not None}

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(_API, params=self._params("user.getinfo", user=self.username))
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    return {"ok": False, "message": data.get("message", "Last.fm error"), "version": None}
                return {"ok": True, "message": "Connected", "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_recent_tracks(self, from_ts: int | None = None, limit: int = 200,
                                page: int | None = None) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params(
                "user.getrecenttracks", user=self.username, limit=limit, page=page,
                **({"from": from_ts} if from_ts else {})))
            r.raise_for_status()
            data = r.json()
            tracks = ((data.get("recenttracks") or {}).get("track")) or []
            return tracks if isinstance(tracks, list) else [tracks]

    async def get_top_artists(self, period: str = "overall", limit: int = 200) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params(
                "user.gettopartists", user=self.username, period=period, limit=limit))
            r.raise_for_status()
            data = r.json()
            artists = ((data.get("topartists") or {}).get("artist")) or []
            return artists if isinstance(artists, list) else [artists]

    async def get_similar_artists(self, artist: str, mbid: str | None = None, limit: int = 15) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params(
                "artist.getsimilar", artist=artist, mbid=mbid, limit=limit, autocorrect=1))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return []
            similar = ((data.get("similarartists") or {}).get("artist")) or []
            return similar if isinstance(similar, list) else [similar]

    async def search_artists(self, query: str, limit: int = 8) -> list[dict]:
        """artist.search — name-completion lookup, distinct from
        artist.getsimilar (which needs an exact/autocorrected name to already
        resolve). Response includes a small thumbnail image directly, no
        separate enrichment call needed."""
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params(
                "artist.search", artist=query, limit=limit))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return []
            matches = (((data.get("results") or {}).get("artistmatches") or {}).get("artist")) or []
            return matches if isinstance(matches, list) else [matches]

    async def get_top_tags(self, artist: str, mbid: str | None = None) -> list[str]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params(
                "artist.gettoptags", artist=artist, mbid=mbid, autocorrect=1))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return []
            tags = ((data.get("toptags") or {}).get("tag")) or []
            tags = tags if isinstance(tags, list) else [tags]
            return [t["name"] for t in tags if t.get("name")][:10]

    async def get_artist_info(self, artist: str, mbid: str | None = None) -> dict[str, Any]:
        """artist.getinfo — listener/play counts and tags in one call (v0.101.0).
        Returns {} on any error so ranking can proceed without it."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(_API, params=self._params(
                    "artist.getinfo", artist=artist, mbid=mbid, autocorrect=1))
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    return {}
                a = data.get("artist") or {}
                stats = a.get("stats") or {}
                tags = ((a.get("tags") or {}).get("tag")) or []
                tags = tags if isinstance(tags, list) else [tags]
                return {
                    "listeners": int(stats.get("listeners") or 0),
                    "playcount": int(stats.get("playcount") or 0),
                    "tags": [t["name"] for t in tags if isinstance(t, dict) and t.get("name")][:10],
                    "mbid": a.get("mbid") or None,
                }
        except Exception:
            return {}

    async def get_tag_top_artists(self, tag: str, limit: int = 50) -> list[dict]:
        """tag.gettopartists — the genre explorer's source list."""
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(_API, params=self._params("tag.gettopartists", tag=tag, limit=limit))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return []
            arts = ((data.get("topartists") or {}).get("artist")) or []
            return arts if isinstance(arts, list) else [arts]
