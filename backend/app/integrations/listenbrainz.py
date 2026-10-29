"""ListenBrainz client (v0.101.0): listening history as a source and similar artists as
ranking evidence. `username` is the ListenBrainz user, `api_key` the user token.
Similar-artist lookups use the public "labs" API and need no token. Endpoint shapes
follow ListenBrainz's published API; every call fails soft so a change upstream only
switches the signal off."""
from __future__ import annotations

from typing import Any

import httpx

from app.integrations.base import BaseIntegration

_API = "https://api.listenbrainz.org/1"
_LABS = "https://labs.api.listenbrainz.org"


class ListenBrainzIntegration(BaseIntegration):
    name = "listenbrainz"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_key}"} if self.api_key else {}

    async def test_connection(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "message": "User token missing", "version": None}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{_API}/validate-token", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                if not data.get("valid"):
                    return {"ok": False, "message": data.get("message") or "Token is not valid", "version": None}
                who = data.get("user_name")
                if self.username and who and who.lower() != self.username.lower():
                    return {"ok": False, "message": f"Token belongs to '{who}', not '{self.username}'", "version": None}
                return {"ok": True, "message": f"Connected as {who}", "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_listens(self, min_ts: int, count: int = 1000) -> list[dict]:
        """GET /user/{user}/listens, newest first, paged back to `min_ts`.
        Returns [{id, artist, track, album, mbid, ts, duration_s}]."""
        user = self.username
        if not user:
            return []
        out: list[dict] = []
        max_ts = None
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                for _ in range(10):
                    params: dict[str, Any] = {"count": min(count, 1000), "min_ts": min_ts}
                    if max_ts:
                        params["max_ts"] = max_ts
                    r = await client.get(f"{_API}/user/{user}/listens", params=params, headers=self._headers())
                    r.raise_for_status()
                    listens = (r.json().get("payload") or {}).get("listens") or []
                    if not listens:
                        break
                    for l in listens:
                        meta = l.get("track_metadata") or {}
                        info = meta.get("additional_info") or {}
                        artist, track = meta.get("artist_name"), meta.get("track_name")
                        ts = l.get("listened_at")
                        if not (artist and track and ts):
                            continue
                        mbids = info.get("artist_mbids") or []
                        dur = info.get("duration") or (int(info["duration_ms"] / 1000) if info.get("duration_ms") else None)
                        out.append({"id": f"{ts}:{track}", "artist": artist, "track": track,
                                    "album": meta.get("release_name") or "",
                                    "mbid": mbids[0] if mbids else None, "ts": int(ts), "duration_s": dur})
                    max_ts = int(listens[-1].get("listened_at") or 0)
                    if len(listens) < min(count, 1000) or not max_ts or max_ts <= min_ts:
                        break
        except Exception:
            return out
        return out

    async def similar_artists(self, mbid: str, algorithm: str, limit: int = 30) -> list[dict]:
        """Labs similar-artists: [{name, mbid, score}] best first, [] on any miss."""
        if not mbid:
            return []
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(f"{_LABS}/similar-artists/json",
                                     params={"artist_mbids": mbid, "algorithm": algorithm})
                r.raise_for_status()
                rows = r.json()
                if isinstance(rows, dict):
                    rows = rows.get("data") or []
                out = []
                for row in rows:
                    if not isinstance(row, dict) or not row.get("name"):
                        continue
                    try:
                        score = float(row.get("score") or 0)
                    except (TypeError, ValueError):
                        score = 0.0
                    out.append({"name": row["name"], "mbid": row.get("artist_mbid"), "score": score})
                return out[:limit]
        except Exception:
            return []
