"""LLM gateway client — Python.

Sync + async surfaces. For Mac-side scripts that route through the local
gateway at http://127.0.0.1:8787 (no HMAC) or the public tunnel at
https://llm.fulldigitaltwin.com/v1/messages (HMAC required).

Local Mac usage (most callers):
    from gateway_client import signed_post
    resp = signed_post(
        "http://127.0.0.1:8787/v1/messages",
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 50},
        virtual_key="vk-fdt",
    )

Tunneled (cloud Workers don't use this; they use signedGatewayFetch.js):
    resp = signed_post(
        "https://llm.fulldigitaltwin.com/v1/messages",
        {"messages": [...], "max_tokens": 50},
        virtual_key="vk-foo",
        key_id="worker-foo", secret_hex="<64 hex>",
    )

Errors are raised as GatewayError with the gateway-side error code attached.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any


class GatewayError(Exception):
    """Non-2xx response from the gateway. Carries the upstream error code."""

    def __init__(self, status: int, code: str, message: str, body: dict | None = None):
        super().__init__(f"gateway {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body or {}


USER_AGENT = "llm-gateway-client/1.0 (+https://fulldigitaltwin.com)"


def _sign_headers(
    body_bytes: bytes,
    virtual_key: str,
    key_id: str | None,
    secret_hex: str | None,
) -> dict[str, str]:
    # User-Agent is required when calling through the public tunnel —
    # Cloudflare Bot Fight Mode 1010-blocks the default `Python-urllib/3.x` UA.
    h = {
        "Authorization": f"Bearer {virtual_key}",
        "content-type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if key_id and secret_hex:
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        payload = f"{key_id}\n{ts}\n{body_hash}".encode()
        secret = bytes.fromhex(secret_hex)
        sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        h["x-gateway-key-id"] = key_id
        h["x-gateway-ts"] = ts
        h["x-gateway-sig"] = sig
    return h


def _parse_or_raise(status: int, raw: bytes) -> dict:
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {"_raw": raw[:500].decode("utf-8", errors="replace")}
    if 200 <= status < 300:
        return body
    err = body.get("error") if isinstance(body, dict) else None
    code = (err or {}).get("type") or "unknown_error"
    msg = (err or {}).get("message") or f"HTTP {status}"
    raise GatewayError(status, code, msg, body if isinstance(body, dict) else None)


def signed_post(
    url: str,
    body: dict[str, Any],
    *,
    virtual_key: str,
    key_id: str | None = None,
    secret_hex: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Synchronous POST. Returns parsed JSON or raises GatewayError on non-2xx.

    For local 127.0.0.1 callers, leave key_id/secret_hex unset — the gateway
    only requires HMAC on tunneled paths (cf-ray header).
    """
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = _sign_headers(body_bytes, virtual_key, key_id, secret_hex)
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _parse_or_raise(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        return _parse_or_raise(e.code, raw)
    except urllib.error.URLError as e:
        raise GatewayError(0, "transport_error", str(e.reason)) from e


async def asigned_post(
    url: str,
    body: dict[str, Any],
    *,
    virtual_key: str,
    key_id: str | None = None,
    secret_hex: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Async wrapper — runs signed_post in a thread."""
    return await asyncio.to_thread(
        signed_post,
        url, body,
        virtual_key=virtual_key, key_id=key_id, secret_hex=secret_hex,
        timeout=timeout,
    )
