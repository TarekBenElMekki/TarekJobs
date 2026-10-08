from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..models import JobRecord


class BaseCollector(ABC):
    name = "base"

    def __init__(self, config: dict, settings: dict):
        self.config = config
        self.settings = settings
        timeout = settings.get("collection", {}).get("request_timeout_seconds", 25)
        ua = settings.get("collection", {}).get(
            "user_agent",
            "TTEA/0.1 (personal job-search research)"
        )
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": ua,
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            },
        )

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self.client.close()

    @abstractmethod
    def fetch(self, limit: int) -> list[JobRecord]:
        raise NotImplementedError