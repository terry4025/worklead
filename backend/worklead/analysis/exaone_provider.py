"""LG EXAONE transport (OpenAI-compatible chat completions). Reasoning text is discarded."""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable

import httpx
from pydantic import BaseModel, ValidationError

from .provider import AIFailure

ENDPOINT = "https://api.exaone.ai/v1/chat/completions"
MODEL = "k-exaone_v2.5"
MIN_INTERVAL_SECONDS = 1.1
MAX_RETRIES_429 = 2
MAX_RESPONSE_BYTES = 512 * 1024
TIMEOUT = httpx.Timeout(180.0, connect=5.0, write=10.0, pool=5.0)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema["required"] = list(schema.get("properties", {}))
    schema["additionalProperties"] = False
    return schema


def _retry_after(headers: httpx.Headers) -> float:
    # The server value is untrusted: cap the wait so one reply cannot stall the worker.
    try:
        if "retry-after-ms" in headers:
            value = int(headers["retry-after-ms"]) / 1000
        else:
            value = float(headers.get("retry-after", "2"))
    except (ValueError, OverflowError):
        return 2.0
    return min(max(0.0, value), 10.0) if math.isfinite(value) else 2.0


class ExaoneClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.transport, self._clock, self._sleep = transport, clock, sleep
        self._lock = threading.Lock()
        self._last = -math.inf

    def _pace(self) -> None:
        # The free tier allows one request per second; space request starts across threads.
        with self._lock:
            wait = self._last + MIN_INTERVAL_SECONDS - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()

    def _post(self, key: str, body: bytes) -> tuple[int, httpx.Headers, bytes]:
        try:
            with httpx.Client(transport=self.transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", ENDPOINT, content=body, headers={
                        "Authorization": "Bearer " + key, "Content-Type": "application/json",
                        "Accept-Encoding": "identity"}) as response:
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise AIFailure("response_too_large", "AI 응답이 허용 크기를 초과했습니다")
                        chunks.append(chunk)
                    return response.status_code, response.headers, b"".join(chunks)
        except httpx.TimeoutException:
            raise AIFailure("timeout", "AI 응답 제한 시간을 초과했습니다") from None
        except httpx.HTTPError:
            raise AIFailure("network_error", "AI 제공자 연결에 실패했습니다") from None

    def complete(self, key: str, system: str, user: str, model: type[BaseModel], *,
                 effort: str = "low", max_tokens: int = 2000) -> tuple[BaseModel, tuple[int, int, int]]:
        body = json.dumps({
            "model": MODEL, "max_tokens": max_tokens, "reasoning_effort": effort,
            "temperature": 1.0, "top_p": 0.95, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": model.__name__.lower(), "strict": True, "schema": strict_schema(model)}},
        }, ensure_ascii=False).encode("utf-8")
        for attempt in range(MAX_RETRIES_429 + 1):
            self._pace()
            status, headers, raw = self._post(key, body)
            if status != 429 or attempt == MAX_RETRIES_429:
                break
            self._sleep(_retry_after(headers))
        if status != 200:
            code = {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}.get(status, "provider_http_error")
            raise AIFailure(code, "AI 제공자 요청이 실패했습니다")
        try:
            document = json.loads(raw)
            choice = document["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise AIFailure("response_incomplete", "AI 응답이 완료되지 않았습니다")
            text = _FENCE.sub("", choice["message"]["content"].strip())
            usage = document.get("usage") or {}
            tokens = (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), 0)
            return model.model_validate_json(text), tokens
        except AIFailure:
            raise
        except (ValidationError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise AIFailure("schema_error", "AI 응답 형식이 올바르지 않습니다") from None
