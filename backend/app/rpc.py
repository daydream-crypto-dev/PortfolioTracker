from __future__ import annotations

import itertools
import http.client
import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RpcError(RuntimeError):
    """Raised when an EVM JSON-RPC endpoint rejects or cannot serve a read call."""


class EvmRpcClient:
    """Tiny dependency-free client for read-only EVM JSON-RPC calls."""

    _ids = itertools.count(1)

    def __init__(self, rpc_url: str, timeout_seconds: int = 20) -> None:
        self.rpc_url = rpc_url
        self.timeout_seconds = timeout_seconds

    def eth_call(self, to_address: str, data: str, block: str = "latest") -> str:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "eth_call",
            "params": [{"to": to_checksum_input(to_address), "data": data}, block],
        }
        response = self._request(payload)
        result = response.get("result")
        if not isinstance(result, str):
            raise RpcError(f"RPC eth_call returned an unexpected result for {to_address}")
        return result

    def eth_batch_call(self, calls: List[Dict[str, str]], block: str = "latest") -> List[Optional[str]]:
        payloads = []
        id_order: List[int] = []
        for call in calls:
            request_id = next(self._ids)
            id_order.append(request_id)
            payloads.append(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "eth_call",
                    "params": [
                        {
                            "to": to_checksum_input(call["to"]),
                            "data": call["data"],
                        },
                        block,
                    ],
                }
            )

        response = self._request_raw(payloads)
        if not isinstance(response, list):
            raise RpcError("RPC batch response was not a list")
        responses_by_id = {
            item.get("id"): item
            for item in response
            if isinstance(item, dict)
        }

        results: List[Optional[str]] = []
        for request_id in id_order:
            item = responses_by_id.get(request_id)
            if item is None:
                raise RpcError(f"RPC batch response omitted id {request_id}")
            error = item.get("error")
            if isinstance(error, dict):
                results.append(None)
                continue
            result = item.get("result")
            if result is not None and not isinstance(result, str):
                raise RpcError(f"RPC batch response returned an unexpected result for id {request_id}")
            results.append(result)
        return results

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = self._request_raw(payload)
        if not isinstance(body, dict):
            raise RpcError("RPC endpoint returned a non-object JSON body")
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error
            raise RpcError(f"RPC error: {message}")
        return body

    def _request_raw(self, payload: Any) -> Any:
        request = Request(
            self.rpc_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PortfolioTracker/0.5 (+local)",
            },
        )

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RpcError(f"RPC HTTP {exc.code}: {detail[:300]}") from exc
            except (URLError, http.client.IncompleteRead, TimeoutError) as exc:
                last_error = exc
                if attempt == 2:
                    raise RpcError(f"RPC request failed after retries: {exc}") from exc
                time.sleep(min(2.0, 0.35 * (2 ** attempt)))
            except json.JSONDecodeError as exc:
                raise RpcError("RPC endpoint returned invalid JSON") from exc

        raise RpcError(f"RPC request failed after retries: {last_error}") from last_error


def to_checksum_input(address: str) -> str:
    """Normalize addresses for RPC input without trying to checksum them."""

    normalized = address.strip()
    if not normalized.startswith("0x"):
        normalized = f"0x{normalized}"
    if len(normalized) != 42:
        raise ValueError(f"Invalid EVM address length: {address}")
    int(normalized[2:], 16)
    return normalized


def encode_address_arg(address: str) -> str:
    return to_checksum_input(address)[2:].lower().rjust(64, "0")


def encode_uint256_arg(value: int) -> str:
    if value < 0:
        raise ValueError("uint256 arguments cannot be negative")
    return hex(value)[2:].rjust(64, "0")


def encode_bool_arg(value: bool) -> str:
    return encode_uint256_arg(1 if value else 0)


def call_data(selector: str, address_args: Optional[List[str]] = None) -> str:
    selector_value = selector.removeprefix("0x")
    encoded_args = "".join(encode_address_arg(address) for address in address_args or [])
    return f"0x{selector_value}{encoded_args}"


def decode_uint256(result: str) -> int:
    if result in ("", "0x"):
        return 0
    return int(result, 16)


def decode_uint256_words(result: str) -> List[int]:
    raw = result.removeprefix("0x")
    if not raw:
        return []
    if len(raw) % 64 != 0:
        raise RpcError("RPC uint256 tuple response had invalid word length")
    return [int(raw[index:index + 64], 16) for index in range(0, len(raw), 64)]


def decode_address_word(word: int) -> str:
    return "0x" + f"{word:064x}"[-40:]


def decode_address_words(result: str) -> List[str]:
    return [decode_address_word(word) for word in decode_uint256_words(result)]


def decode_address_array(result: str) -> List[str]:
    words = decode_uint256_words(result)
    if not words:
        return []
    offset_words = words[0] // 32
    if offset_words >= len(words):
        raise RpcError("RPC address array response had invalid offset")
    length = words[offset_words]
    start = offset_words + 1
    end = start + length
    if end > len(words):
        raise RpcError("RPC address array response ended before declared length")
    return [decode_address_word(word) for word in words[start:end]]


def decode_string(result: str) -> str:
    words = decode_uint256_words(result)
    if not words:
        return ""
    offset_words = words[0] // 32
    if offset_words >= len(words):
        raise RpcError("RPC string response had invalid offset")
    byte_length = words[offset_words]
    raw = result.removeprefix("0x")
    byte_start = (offset_words + 1) * 64
    byte_end = byte_start + (byte_length * 2)
    if byte_end > len(raw):
        raise RpcError("RPC string response ended before declared length")
    return bytes.fromhex(raw[byte_start:byte_end]).decode("utf-8", errors="replace")
