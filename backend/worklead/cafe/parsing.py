"""Small, conservative parsers for the DOM observed during cafe M0.

These functions deliberately do not infer account identity, permission to advertise,
absolute dates, missing comments, search URLs, or region filters.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

CAFE_ORIGIN = "https://cafe.daangn.com"
CATEGORY_PATHS = ("/categories/career", "/categories/it-mobile", "/categories/neighborhood")
RESERVED = {"api", "debug", "errors", "-", "categories", "robots.txt", "favicon.ico"}
PROMOTION_BANS = ("홍보 금지", "광고 금지", "외부 링크 금지", "구직글 금지")
BOARD_WORDS = ("구직", "구인구직", "알바 구인", "홍보", "재능", "프리랜서", "외주")


class CafePageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_cafe_url(url: str, *, home_only: bool = False) -> str:
    """Exact HTTPS origin; no credentials, ports, queries, or encoded traversal."""
    try:
        p = urlsplit(url)
        decoded = unquote(p.path)
        if (p.scheme != "https" or p.hostname != "cafe.daangn.com" or p.username or p.password
                or p.port is not None or p.query or p.fragment or "\\" in url
                or any(part in (".", "..") for part in decoded.split("/"))
                or any(ord(c) < 32 for c in url) or "%" in decoded):
            raise ValueError
        parts = decoded.strip("/").split("/")
        if any(s in RESERVED for s in parts[:1]) and decoded not in CATEGORY_PATHS and decoded != "/":
            raise ValueError
        if home_only and (len(parts) != 1 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", parts[0])
                          or parts[0] in RESERVED):
            raise ValueError
        if not home_only and len(parts) > 1 and parts[0] != "categories" and parts[1] not in ("boards", "posts", "notices"):
            raise ValueError
    except ValueError:
        raise CafePageError("invalid_cafe_url", "당근 카페의 HTTPS 주소만 사용할 수 있습니다") from None
    return CAFE_ORIGIN + (p.path.rstrip("/") or "/")


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(child.text() if isinstance(child, Node) else child for child in self.children).strip()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class _Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict((k, v or "") for k, v in attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag not in {"script", "style"} and data.strip():
            self.stack[-1].children.append(data.strip())


def _nodes(html: str) -> list[Node]:
    return list(_Tree(html).root.walk())


def detect_session(html: str, *, status: int | None = 200) -> tuple[str, str | None]:
    nodes = _nodes(html)
    # Only visible text is used. User comments mentioning CAPTCHA must not stop
    # the account; challenge markers must belong to a gate/dialog/form.
    gates = [n for n in nodes if n.tag in {"form", "dialog", "h1", "h2"}
             or n.attrs.get("role") == "dialog" or any(k in n.attrs.get("class", "").lower()
             for k in ("captcha", "challenge"))]
    gate_text = " ".join(n.text() for n in gates).lower()
    if status in (401, 403, 429):
        return "needs_user", f"http_{status}"
    markers = ("captcha", "자동입력 방지", "본인 인증", "본인인증", "계정이 제한", "약관에 동의", "인증코드가 만료", "verify you are a human")
    if any(marker in gate_text for marker in markers):
        return "needs_user", "authentication_challenge"
    if any(n.attrs.get("aria-label") == "사용자 메뉴" for n in nodes):
        return "logged_in", None
    if any(n.tag in {"button", "a"} and n.text() == "로그인" for n in nodes):
        return "logged_out", "login_required"
    return "needs_user", "session_unrecognized"


def directory_links(html: str, page_url: str, keywords: list[str]) -> list[dict]:
    """Filter rendered cafe cards locally; no unsupported search URL is invented."""
    result = {}
    for n in _nodes(html):
        if n.tag != "a" or not n.attrs.get("href"):
            continue
        try:
            url = safe_cafe_url(urljoin(page_url, n.attrs["href"]), home_only=True)
        except CafePageError:
            continue
        label = n.text()
        if not label or label in {"카페", "홈", "당근"}:
            continue
        if keywords and not any(word.casefold() in label.casefold() for word in keywords):
            continue
        result[url] = {"url": url, "name": label[:200]}
    return list(result.values())


def classify_board(name: str, evidence: str = "") -> dict:
    compact = re.sub(r"\s+", "", evidence)
    banned = next((phrase for phrase in PROMOTION_BANS if phrase.replace(" ", "") in compact), None)
    candidate = any(word in name for word in BOARD_WORDS)
    kind = "job_seek" if candidate else ("free" if "자유" in name else "job_offer" if "구인" in name else "other")
    # A board name is a candidate, never permission. An explicit allowance in
    # inspected rules is necessary, and a prohibition always wins.
    negative = re.search(r"(?:구직글|홍보글|서비스\s*홍보|외주\s*홍보)[^.!?\n]{0,60}(?:금지|불가|허용하지|허용되지|불허)", evidence)
    allowed = not banned and not negative and candidate and bool(re.search(r"(?:구직글|홍보글|서비스 홍보|외주 홍보)\s*(?:을|를|은|는)?\s*(?:게시|등록)?\s*(?:가능|허용)(?:합니다|됩니다|해요|한다|됨)?(?=$|[\s.!?。])", evidence))
    banned = banned or (negative.group(0) if negative else None)
    return {"kind": kind, "post_allowed": allowed, "evidence": banned or evidence[:1000], "candidate": candidate}


def cafe_home(html: str, url: str, *, category: str | None = None, rule_texts: list[str] | None = None) -> dict:
    url = safe_cafe_url(url, home_only=True)
    nodes = _nodes(html)
    # The observed home profile uses h2; h1 is retained for a future rendering
    # only if its text is present, never from URL or document title.
    headings = [n for n in nodes if n.tag in {"h1", "h2"} and n.text()]
    if not headings:
        raise CafePageError("cafe_parse_failed", "카페 이름을 확인하지 못했습니다")
    name = headings[0].text()[:200]
    text = _Tree(html).root.text()
    def number(label):
        found = re.search(label + r"\s*([\d,]+)\s*(?:명|개)", text)
        return int(found.group(1).replace(",", "")) if found else None
    boards = []
    for n in nodes:
        if n.tag != "a" or not n.attrs.get("href"):
            continue
        href = urljoin(url + "/", n.attrs["href"])
        try:
            href = safe_cafe_url(href)
        except CafePageError:
            continue
        if urlsplit(href).path.startswith(urlsplit(url).path + "/boards/"):
            rule = classify_board(n.text(), "\n".join(rule_texts or []))
            boards.append({"name": n.text()[:200], "url": href, **{k: v for k, v in rule.items() if k != "candidate"}})
    can_write = any(n.tag == "a" and urlsplit(urljoin(url, n.attrs.get("href", ""))).path == urlsplit(url).path + "/posts/new" for n in nodes)
    needs_join = any(n.tag == "button" and n.text() == "카페 가입하기" for n in nodes)
    joined = True if can_write and not needs_join else False if needs_join else None
    rules = "\n".join(rule_texts or [])
    prohibition = next((p for p in PROMOTION_BANS if p.replace(" ", "") in re.sub(r"\s+", "", rules)), None)
    status = "excluded" if prohibition else "needs_join" if needs_join else "eligible" if joined and any(b["post_allowed"] for b in boards) else "candidate"
    reason = prohibition or ("사용자 가입 필요" if needs_join else "명시적 구직·홍보 허용 규칙 확인" if status == "eligible" else "구직·홍보 허용 규칙 미확인; 게시 승인 대기")
    category_name = category or next((n.text() for n in nodes if n.text() in {"직업/커리어", "IT/모바일", "동네/지역"}), None)
    region = next((n.text() for n in nodes if n.tag in {"span", "p"} and re.fullmatch(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주도)(?:\s+[가-힣]+[시구군동])?", n.text())), None)
    return {"url": url, "name": name, "region": region, "category": category_name,
            "member_count": number("멤버"), "post_count": number("게시글"), "joined": joined,
            "status": status, "status_reason": reason, "rule_evidence": rules[:3000], "boards": boards}


def notice_links(html: str, cafe_url: str) -> list[str]:
    base = urlsplit(cafe_url).path + "/posts/"
    result = []
    for n in _nodes(html):
        if n.tag == "a" and n.attrs.get("href"):
            try:
                url = safe_cafe_url(urljoin(cafe_url + "/", n.attrs["href"]))
            except CafePageError:
                continue
            if urlsplit(url).path.startswith(base) and not url.endswith("/new") and url not in result:
                result.append(url)
    return result


def promotion_rule_sentences(html: str) -> list[str]:
    """Retain only rule sentences from article body, never author/profile text."""
    result = []
    for n in _nodes(html):
        if "article-body" in n.attrs.get("class", "").split():
            for sentence in re.split(r"(?<=[.!?。])\s+|\n", n.text()):
                if re.search(r"홍보|광고|구직글|외부\s*링크", sentence):
                    result.append(sentence[:1000])
    return result[:20]


RELATIVE_TIME = re.compile(r'^(방금(?:\s*전)?|오늘|어제|\d+\s*(?:초|분|시간|일|주|개월|달|년)\s*전)$')


def relative_date(raw: str | None, now: datetime):
    """Displayed relative times are estimates, never promoted to exact dates."""
    from ..analysis.types import DateInfo
    raw = (raw or '').strip()
    if not RELATIVE_TIME.fullmatch(raw):
        return DateInfo(None, 'unknown', raw or None)
    compact = re.sub(r'\s+', '', raw)
    if compact in ('방금', '방금전', '오늘'): delta = timedelta()
    elif compact == '어제': delta = timedelta(days=1)
    else:
        count, unit = re.fullmatch(r'(\d+)(초|분|시간|일|주|개월|달|년)전', compact).groups()
        seconds = {'초': 1, '분': 60, '시간': 3600, '일': 86400, '주': 604800,
                   '개월': 30 * 86400, '달': 30 * 86400, '년': 365 * 86400}[unit]
        delta = timedelta(seconds=int(count) * seconds)
    return DateInfo(now - delta, 'approximate', raw)


def template_match(title: str, body: str, template_title: str) -> bool:
    """User-approved exclusion heuristic, independent of author ownership."""
    if title.strip() == template_title.strip(): return True
    # Exact portfolio host, including www/subdomains; no substring lookalikes.
    for candidate in re.findall(r'https?://[^\s<>"\']+|(?<![\w.-])(?:www\.)?seyj\.kr(?![\w.-])(?:/[^\s<>"\']*)?', body, re.I):
        host = urlsplit(candidate if '://' in candidate else 'https://' + candidate).hostname
        if host and (host.lower() == 'seyj.kr' or host.lower().endswith('.seyj.kr')): return True
    return False


def board_posts(html: str, page_url: str) -> list[dict]:
    """Only rendered observed post cards; no inferred pagination/API URLs."""
    prefix = '/' + urlsplit(page_url).path.strip('/').split('/')[0] + '/posts/'
    result = {}
    for node in _nodes(html):
        if node.tag != 'a' or not node.attrs.get('href'): continue
        try: url = safe_cafe_url(urljoin(page_url, node.attrs['href']))
        except CafePageError: continue
        if not urlsplit(url).path.startswith(prefix) or url.endswith('/new'): continue
        titles = [n for n in node.walk() if n.tag == 'p' and 'line-clamp-2' in n.attrs.get('class', '').split()]
        # Sanitized/local DOM may retain semantic headings instead of typography.
        if not titles: titles = [n for n in node.walk() if n.tag in ('h2', 'h3')]
        if not titles: continue
        dates = [n.text() for n in node.walk() if n.tag in ('span', 'p', 'time') and RELATIVE_TIME.fullmatch(n.text())]
        result[url] = {'url': url, 'title': titles[0].text(), 'written_raw': dates[-1] if dates else None}
    return list(result.values())


def board_listing_ready(html: str, page_url: str) -> bool:
    nodes = _nodes(html)
    # Hydrated cafe navigation distinguishes a list from a loading skeleton,
    # unrelated route, or a changed page structure.
    hydrated = any(n.tag == 'nav' and n.attrs.get('aria-label') == '게시판' for n in nodes)
    heading = any(n.tag in ('h1', 'h2') and n.text() for n in nodes)
    if not hydrated or not heading: return False
    if board_posts(html, page_url): return True
    # Observed empty-board copy; absence of recognized cards alone is unsafe.
    return any(n.tag in ('p', 'div') and n.text().rstrip('.') == '작성된 게시글이 없어요' for n in nodes)


def post_relative_time(html: str) -> str | None:
    """Post metadata is before the article body; comment times are excluded."""
    nodes = _nodes(html)
    displayed = [n.text() for n in nodes if n.tag == 'span' and 'seed-tag-group-item__label--size_t3' in n.attrs.get('class', '').split() and RELATIVE_TIME.fullmatch(n.text())]
    if displayed: return displayed[0]
    start = next((i for i, n in enumerate(nodes) if n.tag == 'h1'), None)
    if start is None: return None
    end = next((i for i in range(start + 1, len(nodes)) if 'article-body' in nodes[i].attrs.get('class', '').split()), len(nodes))
    dates = [n.text() for n in nodes[start:end] if n.tag in ('p', 'span', 'time') and RELATIVE_TIME.fullmatch(n.text())]
    return dates[0] if dates else None
