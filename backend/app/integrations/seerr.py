from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class SeerrIntegration(BaseIntegration):
    """Seerr (formerly Overseerr) — media request management. Used to protect
    recently-requested items from deletion suggestions, not for deletion control."""
    name = "seerr"

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _base(self) -> str:
        return f"{self.url}/api/v1"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/status", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return {"ok": True, "message": "Connected", "version": data.get("version")}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_requested_titles(self, take: int = 200) -> list[dict]:
        """Recent requests resolved to titles: [{title, year, media_type}].
        Titles come from Seerr's TMDB proxy endpoints; failures on individual
        lookups are skipped rather than failing the whole sync."""
        results = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/request", headers=self._headers(),
                                 params={"take": take, "skip": 0, "sort": "added"})
            r.raise_for_status()
            requests = r.json().get("results", [])

            seen: set[tuple[str, int]] = set()
            for req in requests:
                media = req.get("media") or {}
                media_type = media.get("mediaType")
                tmdb_id = media.get("tmdbId")
                if not media_type or not tmdb_id or (media_type, tmdb_id) in seen:
                    continue
                seen.add((media_type, tmdb_id))
                try:
                    detail_path = "movie" if media_type == "movie" else "tv"
                    d = await client.get(f"{self._base()}/{detail_path}/{tmdb_id}", headers=self._headers())
                    d.raise_for_status()
                    detail = d.json()
                    title = detail.get("title") or detail.get("name")
                    date = detail.get("releaseDate") or detail.get("firstAirDate") or ""
                    year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
                    if title:
                        results.append({"title": title, "year": year, "media_type": media_type})
                except Exception:
                    continue
        return results
