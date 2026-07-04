from abc import ABC, abstractmethod
from typing import Any


class BaseIntegration(ABC):
    """All integrations extend this. Adding a new service = subclass + register in integrations API."""

    name: str = ""

    def __init__(self, url: str, api_key: str, extra_config: dict | None = None):
        self.url = url.rstrip("/") if url else ""
        self.api_key = api_key or ""
        self.extra_config = extra_config or {}

    @abstractmethod
    async def test_connection(self) -> dict[str, Any]:
        """Return {"ok": bool, "message": str, "version": str | None}"""
        ...

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
