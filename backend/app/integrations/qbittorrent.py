from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class QbittorrentIntegration(BaseIntegration):
    """qBittorrent WebUI API. The Integration row's api_key holds "username:password"."""
    name = "qbittorrent"

    def _creds(self) -> tuple[str, str]:
        user, _, password = (self.api_key or "").partition(":")
        return user, password

    async def _login(self, client: httpx.AsyncClient) -> bool:
        user, password = self._creds()
        r = await client.post(f"{self.url}/api/v2/auth/login",
                              data={"username": user, "password": password})
        return r.status_code == 200 and r.text.strip().lower() == "ok."

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                if not await self._login(client):
                    return {"ok": False, "message": "Login failed — api_key must be username:password", "version": None}
                r = await client.get(f"{self.url}/api/v2/app/version")
                r.raise_for_status()
                return {"ok": True, "message": "Connected", "version": r.text.strip()}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def delete_download(self, torrent_hash: str, delete_files: bool = True) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                if not await self._login(client):
                    return {"ok": False, "message": "qBittorrent login failed"}
                r = await client.post(f"{self.url}/api/v2/torrents/delete",
                                      data={"hashes": torrent_hash.lower(),
                                            "deleteFiles": "true" if delete_files else "false"})
                if r.status_code == 200:
                    return {"ok": True, "message": "Torrent removed from qBittorrent"}
                return {"ok": False, "message": f"qBittorrent delete failed: HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}
