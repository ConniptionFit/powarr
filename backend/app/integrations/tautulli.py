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
