from datetime import datetime, timedelta
from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class TautulliIntegration(BaseIntegration):
    name = "tautulli"

    def _api_url(self) -> str:
        return f"{self.url}/api/v2"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(
                    self._api_url(),
                    params={"apikey": self.api_key, "cmd": "get_activity"},
                )
                if not r.is_success:
                    try:
                        body = r.json()
                        msg = body.get("response", {}).get("message") or r.text[:200]
                    except Exception:
                        msg = r.text[:200]
                    return {"ok": False, "message": f"HTTP {r.status_code}: {msg}", "version": None}

                data = r.json()
                result = data.get("response", {}).get("result")
                if result != "success":
                    msg = data.get("response", {}).get("message", "Unexpected response")
                    return {"ok": False, "message": msg, "version": None}

                return {"ok": True, "message": "Connected", "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_watch_stats(self, rating_key: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(
                    self._api_url(),
                    params={
                        "apikey": self.api_key,
                        "cmd": "get_item_user_stats",
                        "rating_key": rating_key,
                    },
                )
                r.raise_for_status()
                data = r.json().get("response", {}).get("data", [])
                watch_count = sum(d.get("total_plays", 0) for d in data)
                return {"watch_count": watch_count}
        except Exception:
            return {"watch_count": 0}

    async def get_recent_history(self, days: int = 30, length: int = 5000) -> list[dict]:
        """Recent play history rows for multi-user protection (v0.29.0) and
        in-progress protection (LIB-04, v0.54.0).

        One paginated call instead of per-item get_item_user_stats — returns
        [{rating_key, user, friendly_name, date, percent_complete}, ...] where
        date is a unix timestamp (int/str) and percent_complete is Tautulli's
        own 0-100 watch-session completion (missing/non-numeric → 0). Fail-soft → [].
        """
        after = int((datetime.utcnow() - timedelta(days=max(1, days))).timestamp())
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                r = await client.get(
                    self._api_url(),
                    params={
                        "apikey": self.api_key,
                        "cmd": "get_history",
                        "after": after,
                        "length": min(max(1, length), 10000),
                    },
                )
                r.raise_for_status()
                data = r.json().get("response", {}).get("data", {})
                rows = data.get("data") if isinstance(data, dict) else data
                if not isinstance(rows, list):
                    return []
                out = []
                for row in rows:
                    rk = row.get("rating_key")
                    if rk is None:
                        continue
                    try:
                        percent_complete = float(row.get("percent_complete") or 0)
                    except (TypeError, ValueError):
                        percent_complete = 0.0
                    out.append({
                        "rating_key": str(rk),
                        "user": row.get("user") or "",
                        "friendly_name": row.get("friendly_name") or row.get("user") or "",
                        "date": row.get("date"),
                        "percent_complete": percent_complete,
                    })
                return out
        except Exception:
            return []
