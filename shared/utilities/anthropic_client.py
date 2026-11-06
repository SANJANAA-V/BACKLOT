"""Minimal Anthropic Messages API client: env-based credentials, rate-limit retries, no secrets in errors."""
from __future__ import annotations

import os
import time
from typing import Callable

import httpx

URL = "https://api.anthropic.com/v1/messages"
RETRY_STATUS = {429, 503, 529}


class ProviderUnavailable(Exception):
    """Transient/absent provider: caller should degrade gracefully."""


class ProviderError(Exception):
    """Non-retryable provider failure."""


class AnthropicClient:
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None, max_retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0) -> None:
        self._key, self.model, self.max_retries, self._sleep = api_key, model, max_retries, sleep
        self._http = client or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls, model_env: str = "LLM_MODEL", **kw) -> "AnthropicClient | None":
        key = os.getenv("ANTHROPIC_API_KEY")
        return cls(key, os.getenv(model_env, "claude-sonnet-5-5"), **kw) if key else None

    def complete(self, system: str, content: str | list[dict], max_tokens: int = 1000) -> str:
        body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": content}]}
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.post(URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise ProviderUnavailable(f"network error: {type(exc).__name__}") from None
                self._sleep(min(0.5 * 2 ** attempt, 20))
                continue
            if r.status_code in RETRY_STATUS:
                if attempt == self.max_retries:
                    raise ProviderUnavailable(f"HTTP {r.status_code} after {attempt + 1} attempts")
                try:
                    delay = float(r.headers.get("retry-after", ""))
                except ValueError:
                    delay = 0.5 * 2 ** attempt
                self._sleep(min(delay, 20))
                continue
            if r.status_code in (401, 403):
                raise ProviderError("authentication failed (check ANTHROPIC_API_KEY)")
            if r.status_code != 200:
                raise ProviderError(f"HTTP {r.status_code}")
            try:
                return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
            except (KeyError, ValueError, TypeError):
                raise ProviderError("unexpected response shape") from None
        raise ProviderUnavailable("exhausted retries")  # pragma: no cover
