"""robots.txt (RFC 9309) 해석. 가장 긴 규칙 일치, 동률이면 Allow 우선, '*'·'$' 지원.

- 2xx: 규칙 적용
- 4xx: 제한 없음으로 해석 (RFC 9309 §2.3.1.3)
- 5xx·네트워크 오류: 전부 금지로 해석 (§2.3.1.4)
robots.txt 허용은 자동 수집 허가와 같지 않다. 약관·정책 검토(SourcePolicy)는 별도로 필요하다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile("^" + regex + ("$" if anchored else ""))


class RobotsRules:
    def __init__(self, text: str | None, mode: str = "rules"):
        """mode: rules | allow_all | disallow_all"""
        self.mode = mode
        self.groups: list[_Group] = []
        if text and mode == "rules":
            self._parse(text)

    def _parse(self, text: str) -> None:
        current: _Group | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    self.groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if current is None:
                    continue
                if key == "disallow" and value == "":
                    continue  # 빈 Disallow = 제한 없음
                current.rules.append((key == "allow", value))
            else:
                last_was_agent = False

    def _group_for(self, agent: str) -> list[tuple[bool, str]]:
        token = agent.lower().split("/", 1)[0]
        specific = [g for g in self.groups if any(a != "*" and a in token for a in g.agents)]
        if specific:
            return [r for g in specific for r in g.rules]
        star = [g for g in self.groups if "*" in g.agents]
        return [r for g in star for r in g.rules]

    def allowed(self, url: str, agent: str) -> bool:
        if self.mode == "allow_all":
            return True
        if self.mode == "disallow_all":
            return False
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in self._group_for(agent):
            if _pattern_to_regex(pattern).match(path):
                length = len(pattern)
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]

    def summary(self, agent: str) -> str:
        if self.mode != "rules":
            return {"allow_all": "robots.txt 없음(4xx) — 제한 없음으로 해석", "disallow_all": "robots.txt 확인 불가 — 전부 금지로 해석"}[self.mode]
        rules = self._group_for(agent)
        dis = [p for a, p in rules if not a]
        return f"적용 규칙 {len(rules)}개 (Disallow {len(dis)}개)" + (f": {', '.join(dis[:5])}" if dis else "")
