"""Bounded, one-shot OpenAI Responses transport. No source data is an instruction."""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass

import httpx
from pydantic import ValidationError

from .grounding import INSTRUCTIONS, INTENTS, StructuredAnalysis, grounded_output  # noqa: F401
from .provider import AIInput, AIOutput, AIFailure

MODEL = "gpt-4.1-mini-2025-04-14"
PRICE_DATE = "2026-09-30"
PRICE_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
PRICE_VERSION = "openai-standard-gpt4.1mini-2026-09-30"
ENDPOINT = "https://api.openai.com/v1/responses"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_SECONDS = 30.0

@dataclass(frozen=True)
class PreparedRequest:
    body: bytes
    input_bound: int
    max_output: int
    model: str


def price_metadata() -> dict:
    return {"input_usd_per_million": 0.4, "cached_input_usd_per_million": 0.1,
            "output_usd_per_million": 1.6, "source_url": PRICE_URL, "version": PRICE_VERSION}


def estimated_cost(usage: tuple[int, int, int], rate: int) -> tuple[int, int]:
    tokens_in, tokens_out, cached = usage
    # Integer nano-USD avoids float rounding down at a cap boundary.
    nanos = (tokens_in - cached) * 400 + cached * 100 + tokens_out * 1600
    return (nanos + 999) // 1000, (nanos * rate + 999_999_999) // 1_000_000_000


def prepare_request(data: AIInput, model: str, max_output: int) -> PreparedRequest:
    if model != MODEL or type(max_output) is not int or not 256 <= max_output <= 4096:
        raise AIFailure("unsupported_model", "지원하는 모델·출력 한도를 확인하세요")
    schema = StructuredAnalysis.model_json_schema()
    # OpenAI requires nullable fields to be required too, and no extra keys.
    schema["required"] = list(schema["properties"])
    payload = {"model": model, "store": False, "stream": False, "tools": [],
               "max_output_tokens": max_output, "service_tier": "default",
               "instructions": INSTRUCTIONS,
               "input": [{"role": "user", "content": json.dumps(asdict(data), ensure_ascii=False)}],
               "text": {"format": {"type": "json_schema", "name": "worklead_analysis", "strict": True, "schema": schema}}}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise AIFailure("input_too_large", "AI 입력이 허용 크기를 초과했습니다")
    # Every ordinary byte-level text token consumes >=1 UTF-8 byte. Full
    # serialized schema/instructions/input bytes plus 4096 reserves framing.
    # Verified returned usage must still be <= this conservative bound.
    return PreparedRequest(body, len(body) + 4096, max_output, model)


def _usage(document: dict, request: PreparedRequest) -> tuple[int, int, int] | None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return None
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    detail = usage.get("input_tokens_details")
    cached = detail.get("cached_tokens", 0) if isinstance(detail, dict) else 0
    if any(type(v) is not int or v < 0 for v in (inp, out, cached)) or cached > inp:
        return None
    # Keep genuine excess usage to account for it; never clip/hide it.
    return inp, out, cached


class OpenAIProvider:
    engine = "openai"
    model = MODEL
    requires_deferred = True

    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def analyze(self, data: AIInput) -> AIOutput:
        raise AIFailure("deferred_required", "외부 AI는 저장 트랜잭션 밖에서 실행해야 합니다")

    def execute(self, data: AIInput, request: PreparedRequest, key: str) -> AIOutput:
        start = time.monotonic()
        usage = None
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(20.0, connect=5.0, write=5.0, pool=5.0),
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", ENDPOINT, content=request.body,
                                   headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200:
                        code = {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}.get(response.status_code, "provider_http_error")
                        raise AIFailure(code, "AI 제공자 요청이 실패했습니다")
                    chunks, total = [], 0
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise AIFailure("schema_error", "AI 응답 인코딩이 올바르지 않습니다")
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise AIFailure("response_too_large", "AI 응답이 허용 크기를 초과했습니다")
                        if time.monotonic() - start > MAX_SECONDS:
                            raise AIFailure("timeout", "AI 응답 제한 시간을 초과했습니다")
                        chunks.append(chunk)
            document = json.loads(b"".join(chunks))
            if not isinstance(document, dict):
                raise AIFailure("schema_error", "AI 응답 형식이 올바르지 않습니다")
            usage = _usage(document, request)
            if usage is None:
                raise AIFailure("usage_missing", "AI 사용량을 확인할 수 없습니다")
            if document.get("model") != request.model:
                raise AIFailure("model_mismatch", "AI 응답 모델이 요청한 모델과 다릅니다")
            if document.get("service_tier") != "default":
                raise AIFailure("pricing_mismatch", "AI 응답의 요금 계층을 확인할 수 없습니다")
            if usage[0] > request.input_bound or usage[1] > request.max_output:
                raise AIFailure("usage_exceeds_reservation", "AI 사용량이 예약한 입력·출력 한도를 초과했습니다")
            if document.get("status") != "completed":
                raise AIFailure("response_incomplete", "AI 응답이 완료되지 않았습니다")
            texts = []
            for item in document.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                    raise AIFailure("schema_error", "AI 응답 항목이 올바르지 않습니다")
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "refusal":
                        raise AIFailure("refusal", "AI 제공자가 분석을 거부했습니다")
                    if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                        raise AIFailure("schema_error", "AI 응답 내용이 올바르지 않습니다")
                    texts.append(part["text"])
            if len(texts) != 1:
                raise AIFailure("schema_error", "AI 구조화 응답은 하나여야 합니다")
            out = StructuredAnalysis.model_validate_json(texts[0])
            return grounded_output(out, data, usage, math.ceil((time.monotonic()-start)*1000))
        except AIFailure as exc:
            exc.usage = usage
            exc.latency_ms = math.ceil((time.monotonic()-start)*1000)
            raise
        except httpx.TimeoutException:
            raise AIFailure("timeout", "AI 응답 제한 시간을 초과했습니다", usage=usage) from None
        except (ValidationError, ValueError, TypeError, KeyError):
            raise AIFailure("schema_error", "AI 응답 형식이 올바르지 않습니다", usage=usage) from None
        except httpx.HTTPError:
            raise AIFailure("network_error", "AI 제공자 연결에 실패했습니다", usage=usage) from None
        except Exception:
            raise AIFailure("provider_error", "AI 제공자 응답을 처리할 수 없습니다", usage=usage) from None
