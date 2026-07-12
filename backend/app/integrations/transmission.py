from typing import Any, Optional
import httpx

from app.integrations.base import BaseIntegration


class TransmissionIntegration(BaseIntegration):
    """Transmission RPC API. The Integration row's api_key holds "username:password"
    (leave blank if auth is disabled)."""
    name = "transmission"

    def _auth(self) -> Optional[tuple[str, str]]:
        if not self.api_key:
            return None
        user, _, password = self.api_key.partition(":")
        return (user, password)

    def _rpc_url(self) -> str:
        return f"{self.url}/transmission/rpc"

    async def _rpc(self, client: httpx.AsyncClient, payload: dict) -> httpx.Response:
        # Transmission requires a session id obtained via an initial 409 response
        r = await client.post(self._rpc_url(), json=payload, auth=self._auth())
        if r.status_code == 409:
            session_id = r.headers.get("X-Transmission-Session-Id", "")
            r = await client.post(self._rpc_url(), json=payload, auth=self._auth(),
                                  headers={"X-Transmission-Session-Id": session_id})
        return r

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await self._rpc(client, {"method": "session-get"})
                r.raise_for_status()
                version = r.json().get("arguments", {}).get("version")
                return {"ok": True, "message": "Connected", "version": version}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def check_downloads(self, hashes) -> set[str] | None:
        """Which of the given torrent hashes exist here (lowercased subset).
        None = client didn't answer — the caller must NOT infer absence."""
        wanted = [h.lower() for h in hashes if h]
        if not wanted:
            return set()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await self._rpc(client, {"method": "torrent-get",
                                             "arguments": {"ids": wanted, "fields": ["hashString"]}})
                r.raise_for_status()
                data = r.json()
                if data.get("result") != "success":
                    return None
                return {(t.get("hashString") or "").lower()
                        for t in data.get("arguments", {}).get("torrents", [])}
        except Exception:
            return None

    # TR_STATUS_SEED_WAIT (5) / TR_STATUS_SEED (6) — fully downloaded and either
    # actively seeding or queued to seed, as opposed to still downloading/checking.
    _SEEDING_STATUSES = {5, 6}

    async def get_seeding_paths(self) -> set[str] | None:
        """downloadDir/name of every torrent currently seeding or seed-ready
        (LIB-05, protects the underlying files from deletion suggestions).
        None = client didn't answer — the caller must NOT assume safe to delete."""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await self._rpc(client, {"method": "torrent-get",
                                             "arguments": {"fields": ["downloadDir", "name", "status"]}})
                r.raise_for_status()
                data = r.json()
                if data.get("result") != "success":
                    return None
                paths: set[str] = set()
                for t in data.get("arguments", {}).get("torrents", []):
                    if t.get("status") not in self._SEEDING_STATUSES:
                        continue
                    dl_dir = (t.get("downloadDir") or "").rstrip("/")
                    name = t.get("name") or ""
                    if dl_dir and name:
                        paths.add(f"{dl_dir}/{name}")
                return paths
        except Exception:
            return None

    async def delete_download(self, torrent_hash: str, delete_files: bool = True) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await self._rpc(client, {
                    "method": "torrent-remove",
                    "arguments": {"ids": [torrent_hash.lower()], "delete-local-data": delete_files},
                })
                if r.status_code == 200 and r.json().get("result") == "success":
                    return {"ok": True, "message": "Torrent removed from Transmission"}
                return {"ok": False, "message": f"Transmission remove failed: {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}
