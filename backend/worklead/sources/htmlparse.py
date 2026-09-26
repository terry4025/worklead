"""표준 형식 기반 HTML 해석 (사이트별 DOM 선택자 추측 없음).

- schema.org JobPosting (JSON-LD)
- OpenGraph / meta description
- 안전한 텍스트 추출 (스크립트·스타일 제거)
어떤 형식을 쓸지는 사이트 조사 결과(프로필)가 정한다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.canonical: str | None = None
        self.title: str | None = None
        self.text: list[str] = []
        self._in_jsonld = False
        self._buf: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._buf = []
            else:
                self._skip += 1
        elif tag in ("style", "noscript", "template"):
            self._skip += 1
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a:
                self.meta[key] = a["content"]
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href") or None
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "title":
            self._in_title = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "section"):
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._in_jsonld:
                self.jsonld.append("".join(self._buf))
                self._in_jsonld = False
            elif self._skip:
                self._skip -= 1
        elif tag in ("style", "noscript", "template") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._buf.append(data)
        elif self._in_title:
            self.title = (self.title or "") + data
        elif not self._skip:
            self.text.append(data)


def collect(markup: str) -> _Collector:
    c = _Collector()
    c.feed(markup)
    c.close()
    return c


def extract_links(markup: str, base_url: str, pattern: re.Pattern[str]) -> list[str]:
    seen: list[str] = []
    for href in collect(markup).links:
        absolute = urljoin(base_url, href)
        if pattern.search(absolute) and absolute not in seen:
            seen.append(absolute)
    return seen


def html_to_text(fragment: str) -> str:
    """HTML 조각을 안전한 텍스트로 (실행 가능한 HTML 을 저장하지 않는다)."""
    c = collect(f"<div>{fragment}</div>")
    return normalize_text("".join(c.text))


def normalize_text(text: str) -> str:
    text = html.unescape(text).replace("\r\n", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _iter_jsonld(blocks: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                items.append(node)
    return items


def find_job_posting(markup: str) -> dict[str, Any] | None:
    for node in _iter_jsonld(collect(markup).jsonld):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types:
            return node
    return None


def parse_iso(value: Any) -> tuple[datetime | None, str]:
    """ISO 날짜/시각. 날짜만 있으면 precision=day."""
    if not isinstance(value, str) or not value.strip():
        return None, "unknown"
    v = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return datetime.fromisoformat(v + "T00:00:00+09:00").astimezone(UTC), "day"
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None, "unknown"  # 시간대 없는 시각은 확정하지 않는다
        return dt.astimezone(UTC), "exact"
    except ValueError:
        return None, "unknown"


def job_location_text(posting: dict[str, Any]) -> str | None:
    loc = posting.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc] if loc else []
    parts: list[str] = []
    for item in locs:
        if not isinstance(item, dict):
            continue
        addr = item.get("address")
        if isinstance(addr, dict):
            txt = " ".join(str(addr.get(k, "")).strip() for k in ("addressRegion", "addressLocality", "streetAddress") if addr.get(k))
            if txt:
                parts.append(txt)
        elif isinstance(addr, str):
            parts.append(addr)
    return ", ".join(parts) or None
