"""Decode relay records from TanStack Start SSR stream parts.

Since 2026-11-04 jobs.daangn.com no longer embeds window.__RELAY_STORE__.
The same relay record source is serialized by seroval inside
<script data-tsr-stream-part> as a JavaScript expression. Only a literal
subset is decoded: objects, arrays, strings, numbers, !0/!1, null, void 0
and $R[n] slot definitions/references. Calls, functions or any other token
raise SerializedError; nothing is evaluated.
"""
from __future__ import annotations

import re

_SCRIPT = re.compile(r"<script\b[^>]*\bdata-tsr-stream-part\b[^>]*>(.*?)</script>", re.S | re.I)
_ROUTER = re.compile(r"\(\$R=>(?=\$R\[0\]=)")
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_PLAIN = {'"': re.compile(r'[^"\\]*'), "'": re.compile(r"[^'\\]*")}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_LITERALS = (("!0", True), ("!1", False), ("null", None), ("void 0", None))


class SerializedError(ValueError):
    """The stream is missing or uses syntax outside the decoded literal subset."""


class _Reader:
    def __init__(self, text: str, pos: int, slots: dict[int, object]):
        self.text, self.pos, self.slots = text, pos, slots

    def _take(self, token: str) -> bool:
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def value(self) -> object:
        text, pos = self.text, self.pos
        if self._take("$R["):
            end = text.find("]", self.pos)
            if end < 0 or not text[self.pos:end].isdigit():
                raise SerializedError(f"잘못된 참조 (위치 {pos})")
            slot, self.pos = int(text[self.pos:end]), end + 1
            if self._take("="):
                self.slots[slot] = None  # a cycle resolves to None, never recursion
                self.slots[slot] = self.value()
            elif slot not in self.slots:
                raise SerializedError(f"정의되지 않은 참조 $R[{slot}]")
            return self.slots[slot]
        char = text[pos:pos + 1]
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char in _PLAIN:
            return self._string(char)
        for token, literal in _LITERALS:
            if self._take(token):
                return literal
        match = _NUMBER.match(text, pos)
        if match:
            self.pos = match.end()
            raw = match.group()
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        raise SerializedError(f"해석하지 않는 문법 (위치 {pos}): {text[pos:pos + 20]!r}")

    def _object(self) -> dict:
        self.pos += 1
        result: dict = {}
        while not self._take("}"):
            if self.text[self.pos:self.pos + 1] in _PLAIN:
                key = self._string(self.text[self.pos])
            else:
                match = _IDENT.match(self.text, self.pos) or _NUMBER.match(self.text, self.pos)
                if not match:
                    raise SerializedError(f"잘못된 키 (위치 {self.pos})")
                key, self.pos = match.group(), match.end()
            if not self._take(":"):
                raise SerializedError(f"':' 누락 (위치 {self.pos})")
            result[key] = self.value()
            self._take(",")
        return result

    def _array(self) -> list:
        self.pos += 1
        result: list = []
        while not self._take("]"):
            result.append(self.value())
            self._take(",")
        return result

    def _string(self, quote: str) -> str:
        text, pos, out, plain = self.text, self.pos + 1, [], _PLAIN[quote]
        while True:
            end = plain.match(text, pos).end()
            out.append(text[pos:end])
            if end >= len(text):
                raise SerializedError("문자열이 닫히지 않았습니다")
            if text[end] == quote:
                self.pos = end + 1
                # Join UTF-16 surrogate pairs (emoji) escaped as two \\u units.
                return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            code = text[end + 1:end + 2]
            if code == "u" and text[end + 2:end + 3] == "{":
                close = text.index("}", end)
                out.append(chr(int(text[end + 3:close], 16)))
                pos = close + 1
            elif code == "u":
                out.append(chr(int(text[end + 2:end + 6], 16)))
                pos = end + 6
            elif code == "x":
                out.append(chr(int(text[end + 2:end + 4], 16)))
                pos = end + 4
            else:
                out.append(_ESCAPES.get(code, code))
                pos = end + 2


def _find_key(tree: object, key: str) -> object:
    stack, seen = [tree], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            if key in node:
                return node[key]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def relay_records(markup: str) -> dict:
    """Relay records from the router stream part, or SerializedError."""
    slots: dict[int, object] = {}
    for script in _SCRIPT.findall(markup):
        router = _ROUTER.search(script)
        if not router:
            continue
        try:
            tree = _Reader(script, router.end(), slots).value()
        except (IndexError, ValueError, RecursionError) as exc:
            raise SerializedError("TanStack 스트림 데이터를 해석할 수 없습니다") from exc
        records = _find_key(tree, "relayRecords")
        if isinstance(records, dict) and isinstance(records.get("client:root"), dict):
            return records
    raise SerializedError("TanStack 스트림에 relayRecords가 없습니다")
