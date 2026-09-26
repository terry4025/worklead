"""선택적 AI 분석 계층 (AnalysisProvider).

- 규칙 분석은 AI 없이 항상 동작한다.
- 외부 전송은 설정(enabled + external_transfer_consent)이 모두 켜진 경우에만 한다.
- 출력은 스키마로 검증하고, 원문에 없는 예산·마감·재택 허용을 만들지 못하게 허용 필드만 받는다.
- 같은 캐시 키(원문 해시+모델+프롬프트 버전+프로필 버전)로는 다시 요청하지 않는다.
- 실패·예산 초과 시 수집 데이터와 규칙 결과는 보존하고 analysis_status=failed 로 표시한다.
현재 버전에는 실제 외부 제공자 구현이 없다 (NullProvider). 구현 시 이 인터페이스를 따른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

PROMPT_VERSION = "prompt-2026.09.1"


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


class AIFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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
    )


_MASKS = [
    (r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}", "[전화번호]"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "[이메일]"),
    (r"\d{6}[-\s]?[1-4]\d{6}", "[주민번호]"),
]


def mask_personal(text: str) -> str:
    import re

    for pat, repl in _MASKS:
        text = re.sub(pat, repl, text)
    return text
