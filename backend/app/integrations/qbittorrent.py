from typing import Any, Iterable
import httpx

from app.integrations.base import BaseIntegration

# Session cookies cached per (url, username) so short-lived integration
# instances reuse the login instead of hitting /auth/login on every call.
# Cached as a name→value dict, NOT a hardcoded name: qBittorrent ≤5.1 calls the
# cookie "SID", 5.2+ calls it "QBT_SID_<port>". qBittorrent expires sessions
# server-side and answers 403 — _request treats that as the signal to re-login
# once and retry.
_session_cache: dict[tuple[str, str], dict[str, str]] = {}


class QbittorrentIntegration(BaseIntegration):
    """qBittorrent WebUI API — username/password login, SID cookie session.

    Credentials live in the Integration row's username/password columns.
    Legacy rows that packed "username:password" into api_key keep working
    via a read-fallback (api_key is deprecated for this integration)."""
    name = "qbittorrent"

    def _creds(self) -> tuple[str, str]:
        if self.username:
            return self.username, self.password
        user, _, password = (self.api_key or "").partition(":")  # legacy shape
        return user, password

    def _cache_key(self) -> tuple[str, str]:
        return (self.url, self._creds()[0])

    def _client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(timeout=15, follow_redirects=True)
        for name, value in _session_cache.get(self._cache_key(), {}).items():
            client.cookies.set(name, value)
        return client

    async def _login(self, client: httpx.AsyncClient) -> tuple[bool, str]:
        user, password = self._creds()
        r = await client.post(f"{self.url}/api/v2/auth/login",
                              data={"username": user, "password": password})
        # v4.x answers 200 "Ok."/"Fails.", v5.x answers 204 on success and 401
        # on bad credentials — the reliable success signal is the session cookie.
        body = r.text.strip().lower()
        if r.status_code < 400 and "fails" not in body and dict(client.cookies):
            _session_cache[self._cache_key()] = dict(client.cookies)
            return True, "ok"
        if r.status_code == 401 or "fails" in body:
            return False, "qBittorrent login failed — check username/password"
        if r.status_code == 403:
            return False, "qBittorrent refused the login (IP temporarily banned after failed attempts?)"
        return False, f"qBittorrent login failed: HTTP {r.status_code} and no session cookie"

    async def _request(self, client: httpx.AsyncClient, method: str, path: str,
                       **kwargs) -> httpx.Response:
        """Authenticated request: login lazily, re-login once on 403 (expired session)."""
        if not dict(client.cookies):
            ok, msg = await self._login(client)
            if not ok:
                raise RuntimeError(msg)
        r = await client.request(method, f"{self.url}{path}", **kwargs)
        if r.status_code == 403:
            _session_cache.pop(self._cache_key(), None)
            client.cookies = httpx.Cookies()
            ok, msg = await self._login(client)
            if not ok:
                raise RuntimeError(msg)
            r = await client.request(method, f"{self.url}{path}", **kwargs)
        return r

    async def test_connection(self) -> dict[str, Any]:
        user, _ = self._creds()
        if not user:
            return {"ok": False, "message": "Username and password required", "version": None}
        try:
            async with self._client() as client:
                r = await self._request(client, "GET", "/api/v2/app/version")
                r.raise_for_status()
                return {"ok": True, "message": "Connected", "version": r.text.strip().lstrip("v")}
        except RuntimeError as e:  # login failure with a user-facing message
            return {"ok": False, "message": str(e), "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def delete_download(self, torrent_hash: str, delete_files: bool = True) -> dict[str, Any]:
        try:
            async with self._client() as client:
                r = await self._request(client, "POST", "/api/v2/torrents/delete",
                                        data={"hashes": torrent_hash.lower(),
                                              "deleteFiles": "true" if delete_files else "false"})
                if r.status_code == 200:
                    return {"ok": True, "message": "Torrent removed from qBittorrent"}
                return {"ok": False, "message": f"qBittorrent delete failed: HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    async def check_downloads(self, hashes: Iterable[str]) -> set[str] | None:
        """Which of the given torrent hashes exist here (lowercased subset).
        None = client didn't answer — the caller must NOT infer absence."""
        wanted = [h.lower() for h in hashes if h]
        if not wanted:
            return set()
        found: set[str] = set()
        try:
            async with self._client() as client:
                for i in range(0, len(wanted), 50):  # keep GET URLs a sane length
                    r = await self._request(client, "GET", "/api/v2/torrents/info",
                                            params={"hashes": "|".join(wanted[i:i + 50])})
                    r.raise_for_status()
                    found |= {(t.get("hash") or "").lower() for t in r.json()}
        except Exception:
            return None
        return found
