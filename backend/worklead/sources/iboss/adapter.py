"""공개 목록과 공개 의뢰 본문만 읽는다. 연락처/유료 열람 영역은 수집하지 않는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from ...analysis.pay import parse_pay
from ...analysis.types import DateInfo, Ev, PayInfo
from ...engine.http import FetchResult, Fetcher
from ..base import DiscoverResult, DiscoveryPlan, ParsedPost, ParseError, PostNotCollectable, PostRef, TaskSpec
from ..htmlparse import normalize_text
from ..profiled import ProfiledSiteAdapter, TaskFailed

_BASE = "https://www.i-boss.co.kr"
_DETAIL = re.compile(r"/ab-1958-(\d+)")
_PAGE = "PB_1415256624"
_KST = timezone(timedelta(hours=9))
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "tr", "section"}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)

    def find(self, *, tag: str | None = None, cls: str | None = None, ident: str | None = None) -> list[_Node]:
        found = []
        if ((tag is None or self.tag == tag) and (cls is None or cls in self.attrs.get("class", "").split())
                and (ident is None or self.attrs.get("id") == ident)):
            found.append(self)
        for child in self.children:
            if isinstance(child, _Node):
                found.extend(child.find(tag=tag, cls=cls, ident=ident))
        return found

    def text(self) -> str:
        def content(node: _Node) -> str:
            if node.tag in {"script", "style", "noscript", "template"}:
                return ""
            value = "".join(content(c) if isinstance(c, _Node) else c for c in node.children)
            return "\n" + value + "\n" if node.tag in _BLOCK else value
        return normalize_text(content(self))


class _Document(HTMLParser):
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _one(nodes: list[_Node], description: str) -> _Node:
    if len(nodes) != 1:
        raise ParseError(f"아이보스 {description} 구조를 확인할 수 없습니다")
    return nodes[0]


def _visible_find(node: _Node, cls: str) -> list[_Node]:
    if (node.tag in {"script", "style", "template"} or "hidden" in node.attrs
            or node.attrs.get("aria-hidden", "").lower() == "true"
            or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.attrs.get("style", ""), re.I)):
        return []
    found = [node] if cls in node.attrs.get("class", "").split() else []
    for child in node.children:
        if isinstance(child, _Node):
            found.extend(_visible_find(child, cls))
    return found


def _current_closed_cta(article: _Node) -> str | None:
    # These direct layout paths keep body HTML and related-post cards out.
    children = [child for child in article.children if isinstance(child, _Node)]
    layouts = [child for child in children if "content_view" in child.attrs.get("class", "").split()]
    for wrapper in children:
        if wrapper.attrs.get("id") == "ABA-wrapper-box-set":
            layouts.extend(child for child in wrapper.children if isinstance(child, _Node)
                           and "vf_wrap" in child.attrs.get("class", "").split())
    for layout in layouts:
        for contact in layout.children:
            if not isinstance(contact, _Node) or "contact-info" not in contact.attrs.get("class", "").split():
                continue
            for area in _visible_find(contact, "user_info_contact"):
                for button in _visible_find(area, "btn_cash"):
                    if "done" in button.attrs.get("class", "").split() and button.text() == "마감된 의뢰입니다":
                        return "마감된 의뢰입니다"
    return None


def _post_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.i-boss.co.kr":
        return None
    match = _DETAIL.fullmatch(parsed.path)
    return match.group(1) if match else None


def _login_only(root: _Node, url: str) -> bool:
    if root.find(ident="BD1957-view") or root.find(cls="proposal_bd"):
        return False
    return "login" in urlsplit(url).path.lower() or any(
        node.attrs.get("type", "").lower() == "password" for node in root.find(tag="input")
    ) or any(marker in root.text() for marker in ("로그인 후 이용", "로그인이 필요", "로그인하여"))


def _budget(raw: str) -> PayInfo:
    # Labels are public source fields; do not turn an unlabeled budget into fixed project pay.
    raw = re.sub(r"^예산\s*", "", " ".join(raw.split())).strip()
    unit = "unknown"
    for marker, mapped in (("건당", "project"), ("월간", "month"), ("월급", "month"),
                           ("일당", "day"), ("일급", "day"), ("시급", "hour"), ("시간당", "hour")):
        if marker in raw:
            unit = mapped
            break
    parsed = parse_pay("예산 " + re.sub(r"최소\s*|최대\s*", "", raw))
    low, high = parsed.min, parsed.max
    if "최소" in raw and "최대" not in raw and not re.search(r"[~–-]\s*\d", raw):
        high = None
    if "최대" in raw and "최소" not in raw:
        low = None
    return PayInfo(raw=raw, min=low, max=high, unit=unit, negotiable=parsed.negotiable,
                   evidence=[Ev(raw, None, None, "explicit", "high", "아이보스 공개 예산 필드")])


class IbossAdapter(ProfiledSiteAdapter):
    # These are local application limits, not a quota granted by the site.
    discovery_request_limit = 120
    batch_resume_seconds = 60
    persist_discovery_pages = True

    def describe_capabilities(self):
        capabilities = super().describe_capabilities()
        labels = {
            "nationwide_search": "지역 제한 없는 공개 목록",
            "incremental": "중복 제거·이어보기",
            "structured_data": "구조화 데이터(JSON-LD)",
        }
        for capability in capabilities:
            capability.label = labels.get(capability.key, capability.label)
        return capabilities

    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan:
        return DiscoveryPlan(
            tasks=[TaskSpec("iboss-all-public-v1", "all-public", "전체 분류 공개 의뢰")],
            target_label="전체 분류 공개 모집 중 의뢰", region_list_status="not_applicable",
            unit_label="게시판", depth_limit=0,
            notes=["마감 제외 공개 목록의 마지막 페이지까지 이어서 확인합니다.",
                   "제목·분류·검색어로 후보를 버리지 않고 모든 공개 상세 본문을 기존 규칙으로 분석합니다.",
                   "로그인·유료 연락처는 수집하지 않습니다. 비공개·목록 미등재 글과 시장 전체 포괄률은 미확인입니다."],
            query_labels={"iboss-all-public-v1": "전체 분류 · 공개 본문 확인"},
        )

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        cursor = task.cursor or "1"
        if not re.fullmatch(r"[1-9]\d*", cursor):
            raise ParseError("아이보스 페이지 커서가 올바르지 않습니다")
        page = int(cursor)
        url = f"{_BASE}/ab-1957?sort=now" + (f"&{_PAGE}={page}" if page > 1 else "")
        result = fetcher.get(url)
        if result.outcome != "ok":
            raise TaskFailed(result.outcome, f"아이보스 목록 요청 실패 (HTTP {result.status or '-'})")
        document = _Document(result.text or "").root
        if _login_only(document, result.final_url):
            raise TaskFailed("login_required", "아이보스 공개 목록에 로그인 요구가 표시되었습니다")
        board = _one(document.find(cls="proposal_bd"), "공개 목록")
        rows = board.find(cls="cell")
        refs: dict[str, PostRef] = {}
        for row in rows:
            # Restrict to actual list cards; sidebar/popular-post links are not discovery refs.
            text_boxes = row.find(cls="txt")
            if text_boxes:
                text_box = _one(text_boxes, "목록 항목")
            else:
                # The installed application's UA receives a compact public variant:
                # cell > status > a > subject, without the desktop txt wrapper.
                _one(row.find(cls="subject"), "간결한 목록 제목")
                text_box = row
            links = [a for a in text_box.find(tag="a") if _post_id(urljoin(result.final_url, a.attrs.get("href", "")))]
            link = _one(links, "목록 상세 링크")
            if not text_boxes:
                _one(link.find(cls="subject"), "간결한 목록 상세 제목")
            link_url = urljoin(result.final_url, link.attrs["href"])
            post_id = _post_id(link_url)
            assert post_id is not None
            # Headline is only a display hint. No title eligibility or keyword filtering.
            headings = link.find(tag="h4") or link.find(tag="h3") or link.find(cls="subject")
            hint = headings[0].text() if headings else link.text().split("\n")[0]
            refs.setdefault(post_id, PostRef(f"{_BASE}/ab-1958-{post_id}", post_id, hint or None))
        pagers = board.find(cls="ABA-list-paging")
        if not pagers:
            # Compact markup puts pagination beside proposal_bd in this board.
            pagers = _one(document.find(ident="BD1957_LF"), "간결한 공개 목록").find(cls="ABA-list-paging")
        pager = _one(pagers, "페이지 이동")
        forward = []
        listed_pages = []
        page_links = pager.find(tag="a")
        for link in page_links:
            href = urljoin(result.final_url, link.attrs.get("href", ""))
            parts = urlsplit(href)
            query = parse_qs(parts.query)
            raw_page = query.get(_PAGE, [""])[0]
            if not re.fullmatch(r"[1-9]\d*", raw_page):
                continue
            if parts.scheme != "https" or parts.netloc != "www.i-boss.co.kr" or parts.path != "/ab-1957" or query.get("sort") != ["now"]:
                raise ParseError("아이보스 공개 목록 페이지 이동 경로가 변경되었습니다")
            listed_pages.append(int(raw_page))
            if int(raw_page) > page:
                forward.append(int(raw_page))
        reported_last_page = None
        if not rows and not any(marker in board.text() for marker in ("등록된 게시물이 없습니다", "등록된 게시글이 없습니다", "검색된 게시물이 없습니다")):
            # After midnight the active list can shrink during a saved scan.
            # The public page then has a plain empty wrap and explicitly clamps
            # its current-page marker to the last page, below our requested page.
            children = [child for child in board.children if isinstance(child, _Node)]
            current = pager.find(cls="LF_page_link_current")
            empty_wrap = (len(children) == 1 and children[0].attrs.get("class") == "wrap"
                          and not board.text() and all(isinstance(c, str) and not c.strip() for c in children[0].children))
            current_page = int(current[0].text()) if len(current) == 1 and re.fullmatch(r"[1-9]\d*", current[0].text()) else None
            if (empty_wrap and current_page is not None and current_page < page
                    and len(listed_pages) == len(page_links)
                    and all(number <= current_page for number in listed_pages)):
                reported_last_page = current_page
            else:
                raise ParseError("아이보스 목록 항목과 명시적 빈 목록 표시가 없습니다")
        next_page = min(forward) if forward else None
        if next_page is not None and next_page != page + 1:
            raise ParseError("아이보스 다음 페이지를 연속해서 확인할 수 없습니다")
        if next_page is not None and not refs:
            raise ParseError("빈 목록에 다음 페이지가 표시되어 탐색 종료를 확정할 수 없습니다")
        return DiscoverResult(list(refs.values()), str(next_page) if next_page else None, {
            "strategy": "public_board_fulltext", "listed_posts": len(refs), "page": page,
            "next_page": next_page, "has_next": next_page is not None, "body_filter": "all_public",
            "scope": "모집 중 공개 의뢰 · 전체 분류", "title_filter": "none",
            **({"terminal_reason": "requested_page_after_last", "reported_last_page": reported_last_page}
               if reported_last_page is not None else {}),
        })

    def block_detector(self, result: FetchResult) -> str | None:
        root = _Document(result.text or "").root
        return "login_required" if _login_only(root, result.final_url) else None

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost:
        if result.outcome != "ok":
            raise ParseError(f"아이보스 상세 응답이 정상 상태가 아닙니다 ({result.outcome})")
        root = _Document(result.text or "").root
        if _login_only(root, result.final_url):
            raise PostNotCollectable("login_required", "공개 의뢰 본문 대신 로그인 요구가 표시되었습니다")
        post_id = _post_id(result.final_url)
        if not post_id or (ref.source_post_id and ref.source_post_id != post_id) or _post_id(ref.url) != post_id:
            raise ParseError("아이보스 상세 공고 식별자가 요청과 다릅니다")
        article = _one(root.find(ident="BD1957-view"), "의뢰 상세")
        subject = _one(article.find(cls="Article-subject") + article.find(cls="view-head"), "제목 영역")
        title = _one(subject.find(tag="h1"), "제목").text()
        body_nodes = article.find(cls="ABA-view-body")
        if not body_nodes and any(marker in article.text() for marker in ("비공개", "신고 접수된 게시글")):
            raise PostNotCollectable("content_unavailable", "아이보스 의뢰 본문이 공개되지 않았습니다")
        body = _one(body_nodes, "공개 본문").text()
        if not title or not body:
            raise ParseError("아이보스 의뢰 제목 또는 본문이 비어 있습니다")
        if len(body) < 120 and re.fullmatch(r"(?:로그인 후|로그인이 필요|신고 접수된 게시글|비공개 게시글)[^\n]*", body):
            raise PostNotCollectable("content_unavailable", "아이보스 의뢰 본문이 공개되지 않았습니다")
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?\b", subject.text())
        if len(dates) != 1:
            raise ParseError("아이보스 게시 시각을 확인할 수 없습니다")
        try:
            published_at = datetime.fromisoformat(dates[0]).replace(tzinfo=_KST).astimezone(UTC)
        except ValueError as exc:
            raise ParseError("아이보스 게시 시각 형식이 올바르지 않습니다") from exc
        info = _one(article.find(cls="request-info"), "의뢰 조건")
        budget = _budget(_one(info.find(cls="budget"), "예산").text())
        deadline_node = _one(info.find(cls="close_date"), "마감 정보")
        deadline_raw = deadline_node.text()
        # The site's date and badge are adjacent text nodes: 2026.09.30오늘 마감.
        match = re.search(r"(?<!\d)(\d{4})[.-](\d{2})[.-](\d{2})(?!\d)", deadline_raw)
        deadline = DateInfo(raw=deadline_raw)
        status = None
        status_ev = []
        if match:
            try:
                day = datetime(*(int(x) for x in match.groups()), tzinfo=_KST)
            except ValueError as exc:
                raise ParseError("아이보스 마감 날짜 형식이 올바르지 않습니다") from exc
            deadline = DateInfo(day.astimezone(UTC), "day", deadline_raw)
            # A date-only deadline lasts through its entire local calendar day.
            status = "closed" if observed_at.astimezone(_KST).date() > day.date() else "open"
            status_ev.append(Ev(deadline_raw, None, None, "explicit", "high", "아이보스 공개 모집 마감 날짜"))
        # dline belongs to related-post cards, including cards nested inside
        # BD1957-view. The current request's deadline badge and contact CTA are
        # separate authoritative markers; neither includes related-post cards.
        finished = [node for node in deadline_node.find(cls="deadline")
                    if "finished" in node.attrs.get("class", "").split() and node.text() == "마감"]
        contact_closed = _current_closed_cta(article)
        if contact_closed or finished or "[마감]" in title or re.search(r"마감\s*(?:완료|되었습니다)|모집\s*마감|종료된\s*의뢰", deadline_raw):
            status = "closed"
            marker = contact_closed or ("마감" if finished else "[마감]" if "[마감]" in title else deadline_raw)
            status_ev = [Ev(marker, None, None, "explicit", "high", "아이보스 명시적 마감 표시")]
        return ParsedPost(
            title=re.sub(r"^\[마감\]\s*", "", title), body=body,
            original_url=ref.url, canonical_url=f"{_BASE}/ab-1958-{post_id}", source_post_id=post_id,
            published=DateInfo(published_at, "exact", dates[0]), deadline=deadline, pay=budget,
            source_status=status, status_evidence=status_ev,
            contact_channel="아이보스 원문에서 직접 (연락처 열람은 로그인·사이트 정책 적용)",
            parse_notes=["공개 본문과 예산·마감 정보만 수집. 로그인·유료 연락처 영역 제외."],
        )
