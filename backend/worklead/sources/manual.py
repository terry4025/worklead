"""수동 입력 어댑터 (텍스트 / CSV / JSON).

사용자가 적법하게 확보한 글만 넣는다. 정책 제한을 우회하는 수집 기능이 아니다:
URL 을 받아 대신 내려받지 않고, 입력된 텍스트만 공통 파이프라인으로 분석한다.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from ..analysis.dates import parse_relative_published
from ..analysis.types import DateInfo
from .base import Capability, DiscoveryPlan, HealthReport, ParsedPost, ParseError, Revalidation, SourceNotReady
from .htmlparse import normalize_text, parse_iso

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
FIELDS = ("title", "body", "url", "region", "published_at", "published_raw")


class ManualAdapter:
    source_id = "manual"
    name = "수동 입력"
    kind = "manual"
    adapter_version = "0.1.0"
    parser_version = "manual-0.1.0"
    allowed_hosts: set[str] = set()
    daily_request_budget = 0
    min_interval_seconds = 0.0
    scope_note = "직접 확보한 글을 붙여넣거나 CSV·JSON 파일로 입력"
    default_interval_minutes = None

    def describe_capabilities(self) -> list[Capability]:
        return [
            Capability("import_text", "텍스트 붙여넣기", "supported"),
            Capability("import_csv", "CSV 가져오기", "supported", f"최대 {MAX_ROWS}행, {MAX_IMPORT_BYTES // 1024 // 1024}MB"),
            Capability("import_json", "JSON 가져오기", "supported"),
            Capability("revalidate", "상태 재확인", "unsupported", "원문을 대신 내려받지 않습니다"),
        ]

    def healthcheck(self) -> HealthReport:
        return HealthReport("ok", None)

    def block_detector(self, result: Any) -> str | None:
        return None

    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan:
        raise SourceNotReady("unsupported", "수동 입력은 자동 탐색을 하지 않습니다")

    def discover(self, *a: Any, **k: Any):  # pragma: no cover - 선언상 미지원
        raise SourceNotReady("unsupported", "수동 입력은 자동 탐색을 하지 않습니다")

    def fetch_detail(self, *a: Any, **k: Any):  # pragma: no cover
        raise SourceNotReady("unsupported", "수동 입력은 원문을 내려받지 않습니다")

    def parse(self, *a: Any, **k: Any):  # pragma: no cover
        raise SourceNotReady("unsupported", "수동 입력은 parse_import 를 사용합니다")

    def revalidate(self, url: str, fetcher: Any, observed_at: datetime) -> Revalidation:
        return Revalidation(None, "accessible", "수동 입력은 재확인하지 않습니다")

    # ── 가져오기 ─────────────────────────────────────────────────────
    def parse_import(self, fmt: str, content: str, observed_at: datetime, original_url: str | None = None) -> list[ParsedPost]:
        if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ParseError(f"입력이 너무 큽니다 (최대 {MAX_IMPORT_BYTES // 1024 // 1024}MB)")
        if fmt == "text":
            return [self._from_text(content, original_url, observed_at)]
        if fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(content.lstrip("﻿"))))
        elif fmt == "json":
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ParseError(f"JSON 형식 오류: {exc.msg}") from exc
            rows = data if isinstance(data, list) else [data]
        else:
            raise ParseError(f"지원하지 않는 형식: {fmt}")
        if len(rows) > MAX_ROWS:
            raise ParseError(f"행이 너무 많습니다 (최대 {MAX_ROWS})")
        out = []
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ParseError(f"{i}번째 항목이 객체가 아닙니다")
            out.append(self._from_row(row, i, observed_at))
        return out

    def _from_text(self, content: str, url: str | None, observed_at: datetime) -> ParsedPost:
        text = normalize_text(content)
        if len(text) < 10:
            raise ParseError("내용이 너무 짧습니다")
        lines = text.split("\n")
        title = lines[0][:120]
        body = "\n".join(lines[1:]).strip() or text
        return ParsedPost(title=title, body=body, original_url=_safe_url(url), canonical_url=None, source_post_id=None)

    def _from_row(self, row: dict[str, Any], i: int, observed_at: datetime) -> ParsedPost:
        title = normalize_text(str(row.get("title") or ""))
        body = normalize_text(str(row.get("body") or ""))
        if not title or not body:
            raise ParseError(f"{i}번째 행: title·body 가 필요합니다")
        published = DateInfo()
        if row.get("published_at"):
            at, prec = parse_iso(str(row["published_at"]))
            published = DateInfo(at, prec, str(row["published_at"]))  # type: ignore[arg-type]
        elif row.get("published_raw"):
            published = parse_relative_published(str(row["published_raw"]), observed_at)
        return ParsedPost(
            title=title[:200],
            body=body,
            original_url=_safe_url(row.get("url")),
            canonical_url=None,
            source_post_id=None,
            posted_region_raw=(str(row["region"]).strip() or None) if row.get("region") else None,
            published=published,
        )


def _safe_url(url: Any) -> str | None:
    if not url:
        return None
    u = str(url).strip()
    parts = urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return u
