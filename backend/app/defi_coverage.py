from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .adapters import MoralisApiError


class MoralisDefiCoverageClient:
    """Client for Moralis's supported DeFi protocols endpoint."""

    BASE_URL = "https://api.moralis.com/v1"

    def __init__(self, api_key: str, timeout_seconds: int = 20) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def get_supported_protocols(self, chains: str) -> List[Dict[str, Any]]:
        payload = self._request_json("/defi/protocols", {"chains": chains})
        protocols = self._extract_protocol_list(payload)
        if protocols is None:
            raise MoralisApiError("Moralis DeFi protocols response did not include a protocol list")
        return protocols

    def _request_json(self, path: str, params: Dict[str, str]) -> Any:
        query_string = urlencode(params)
        url = f"{self.BASE_URL}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PortfolioTracker/0.4 (+local)",
                "X-Api-Key": self.api_key,
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MoralisApiError(f"Moralis HTTP {exc.code}: {detail[:300]}") from exc
        except URLError as exc:
            raise MoralisApiError(f"Moralis request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise MoralisApiError("Moralis returned invalid JSON") from exc

    def _extract_protocol_list(self, payload: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if not isinstance(payload, dict):
            return None

        for key in ("result", "protocols", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        return None


def protocol_display_name(protocol: Dict[str, Any]) -> str:
    for key in ("name", "protocol_name", "display_name", "displayName", "slug", "id"):
        value = protocol.get(key)
        if value:
            return str(value)
    return "unknown"
