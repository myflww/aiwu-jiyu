"""Reserved adapter for the Love House (爱之屋) long-term memory service.

The first release does not enable long-term memory. This module defines the
contract the desktop app will use once Love House exposes a local HTTP API,
so the rest of the app does not need to change.

Proposed endpoints (to be implemented on the Love House side):

    GET  {endpoint}/health
    POST {endpoint}/v1/memory/remember
    POST {endpoint}/v1/memory/recall
    POST {endpoint}/v1/memory/forget

All payloads are JSON; session_id should be the AstrBot unified message origin
(for example ``qq:FriendMessage:123456789``) so private chats and groups stay
isolated.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class MemoryNotConfigured(RuntimeError):
    pass


class MemoryAdapter:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = (endpoint or "").rstrip("/")

    def _require(self) -> str:
        if not self.endpoint:
            raise MemoryNotConfigured("尚未配置爱之屋地址")
        return self.endpoint

    def _request(self, path: str, payload: dict | None = None, method: str = "POST") -> Any:
        endpoint = self._require()
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def health(self) -> dict:
        return self._request("/health", method="GET")

    def remember(
        self,
        session_id: str,
        persona_id: str,
        text: str,
        metadata: dict | None = None,
    ) -> dict:
        return self._request(
            "/v1/memory/remember",
            {
                "session_id": session_id,
                "persona_id": persona_id,
                "text": text,
                "metadata": metadata or {},
            },
        )

    def recall(self, session_id: str, query: str, limit: int = 6) -> list[dict]:
        result = self._request(
            "/v1/memory/recall",
            {"session_id": session_id, "query": query, "limit": limit},
        )
        items = result.get("items") if isinstance(result, dict) else result
        return items if isinstance(items, list) else []

    def forget(self, session_id: str, memory_id: str) -> bool:
        result = self._request(
            "/v1/memory/forget",
            {"session_id": session_id, "memory_id": memory_id},
        )
        return bool(result.get("ok", True)) if isinstance(result, dict) else True
