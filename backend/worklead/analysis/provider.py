"""선택적 AI 분석 계층 (AnalysisProvider).

- 규칙 분석은 AI 없이 항상 동작한다.
- 외부 전송은 설정(enabled + external_transfer_consent)이 모두 켜진 경우에만 한다.
- 출력은 스키마로 검증하고, 원문에 없는 예산·마감·재택 허용을 만들지 못하게 허용 필드만 받는다.
- 같은 캐시 키(원문 해시+모델+프롬프트 버전+프로필 버전)로는 다시 요청하지 않는다.
- 실패·예산 초과 시 수집 데이터와 규칙 결과는 보존하고 analysis_status=failed 로 표시한다.
실제 외부 제공자는 coordinator가 원문 DB 트랜잭션 밖에서 실행한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

PROMPT_VERSION = "prompt-2026.09.1"
SCHEMA_VERSION = "schema-2026.09.2"


@dataclass
class AIInput:
    title: str
    body: str  # 개인정보 마스킹 후 전달
    categories: list[str]
    rule_summary: str | None


@dataclass
class AIOutput:
    summary: str | None
    deliverables: list[str] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    #: 확인 가능한 비용만 (모르면 None)
    cost_krw: int | None = None
    suggested_intent: str = "unknown"
    evidence_quotes: list[str] = field(default_factory=list)
    cached_tokens: int | None = None
    estimated_usd_micros: int | None = None
    estimated_cost_krw: int | None = None
    reserved_cost_krw: int | None = None
    cost_basis: str | None = None
    price_version: str | None = None
    usd_to_krw_rate: int | None = None


class AIFailure(Exception):
    def __init__(self, code: str, message: str, *, usage: tuple[int, int, int] | None = None, latency_ms: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.usage = usage
        self.latency_ms = latency_ms


class AnalysisProvider(Protocol):
    engine: str
    model: str | None

    def available(self) -> bool: ...

    def analyze(self, data: AIInput) -> AIOutput: ...


class NullProvider:
    engine = "none"
    model = None

    def available(self) -> bool:
        return False

    def analyze(self, data: AIInput) -> AIOutput:  # pragma: no cover
        raise AIFailure("not_configured", "AI 제공자가 설정되지 않았습니다")


def validate_output(out: AIOutput) -> AIOutput:
    """허용 필드·길이만 통과. 자유 텍스트 점수나 상태는 받지 않는다."""

    def clean(items: list[str], limit: int = 6) -> list[str]:
        return [str(x).strip()[:200] for x in items if str(x).strip()][:limit]

    if out.summary is not None and not isinstance(out.summary, str):
        raise AIFailure("schema_error", "summary 형식 오류")
    return AIOutput(
        summary=(out.summary or "").strip()[:300] or None,
        deliverables=clean(out.deliverables),
        tech=clean(out.tech),
        questions=clean(out.questions, 5),
        uncertain=clean(out.uncertain),
        tokens_in=out.tokens_in,
        tokens_out=out.tokens_out,
        latency_ms=out.latency_ms,
        cost_krw=out.cost_krw,
        suggested_intent=out.suggested_intent,
        evidence_quotes=clean(out.evidence_quotes),
        cached_tokens=out.cached_tokens,
        estimated_usd_micros=out.estimated_usd_micros,
        estimated_cost_krw=out.estimated_cost_krw,
        reserved_cost_krw=out.reserved_cost_krw,
        cost_basis=out.cost_basis,
        price_version=out.price_version,
        usd_to_krw_rate=out.usd_to_krw_rate,
    )


_MASKS = [
    (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [비밀값]"),
    (r"(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}", "[API키]"),
    (r"(?i)\b(?:api[_ -]?key|access[_ -]?token|password|비밀번호|계좌번호)\s*[:=]\s*[^\s,;]+", "[비밀값]"),
    (r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}", "[전화번호]"),
    (r"(?<!\d)0(?:2|[3-6][1-5]|70)[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)", "[전화번호]"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "[이메일]"),
    (r"\d{6}[-\s]?[1-4]\d{6}", "[주민번호]"),
    (r"(?im)^(?:\s*(?:성명|이름|연락처|전화번호|주소|근무지 주소|상세 주소)\s*[:：]\s*).+$", "[개인정보 항목]"),
    (r"https?://[^\s<>\"']+", "[URL]"),
]


def mask_personal(text: str) -> str:
    import re

    for pat, repl in _MASKS:
        text = re.sub(pat, repl, text)
    return text


def assist_eligible(ra) -> bool:
    """AI help is for non-excluded software work paid per project, or time-pay posts under review."""
    time_pay = any(getattr(f, "key", None) == "time_pay" for f in getattr(ra, "factors", ()))
    return ra.recommendation != "excluded" and ra.dev_relevant and (ra.pay.unit == "project" or time_pay)
