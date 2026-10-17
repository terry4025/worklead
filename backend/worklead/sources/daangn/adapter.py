"""Anonymous public HTML adapter, verified against the 2026-09-28 site.

Region feeds (/s?regionId=, first 20 only) were verified on 2026-10-17;
the web client fixes regionRange and pages only through GraphQL.

Only the downloaded document is parsed. Embedded JSON is decoded as data,
never evaluated; no GraphQL, login session or pagination API is requested.
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode, urlsplit

from ...analysis.pay import parse_pay
from ...analysis.types import DateInfo, Ev
from ...engine.http import FetchResult, Fetcher
from ..base import DiscoverResult, DiscoveryPlan, ParsedPost, ParseError, PostNotCollectable, PostRef, SourceNotReady, TaskSpec
from ..htmlparse import collect, normalize_text, parse_iso
from ..profiled import ProfiledSiteAdapter, TaskFailed, canonicalize, check_profile


# Explicit technical variants, not fuzzy matches or unrestricted expansion.
# Expansion starts from enabled query terms; an Excel-only query stays scoped.
_TITLE_ALIASES = (
    ("홈페이지", "홈피", "웹사이트", "웹페이지", "웹개발", "웹제작", "웹서비스", "사이트", "website", "webpage", "상세페이지", "상품페이지", "소개페이지"),
    ("프로그램", "소프트웨어"),
    ("파이썬", "python"),
    ("엑셀", "excel"),
    ("앱", "어플", "애플리케이션"),
    ("자동화", "rpa"),
    ("ai에이전트", "인공지능에이전트", "aiagent"),
    ("챗봇", "chatbot"),
    ("봇", "bot"),
)
_GENERIC_QUERY_TERMS = frozenset({
    "구합니다", "구해요", "모집", "찾습니다", "찾아요", "필요", "가능", "재택", "온라인",
    "의뢰", "외주", "견적", "요청", "부탁", "제작", "개발", "수정", "구축", "쇼핑몰",
    "사이트", "website", "webpage",
    "만들어주실", "제작해주실", "개발해주실", "수정해주실", "도와주실", "도와주세요",
})
_WEAK_CREATION_REQUEST = re.compile(
    r"(?:제작|개발|수정|구축|리뉴얼|유지보수)\s*(?:을|를|좀)?\s*(?:의뢰|외주|요청|부탁|도와\s*(?:주실|주세요))"
    r"|(?:만들어|제작해|개발해|수정해)\s*(?:주실|줄|주세요)"
    r"|외주\s*(?:의뢰|요청|맡기|드립니다|구합니다)"
)
# Only promote a named software deliverable with a creation/change request.
# A site link, an agent vacancy or a technology name alone is not this signal.
_SOFTWARE_TARGET = (
    r"(?:웹\s*(?:사이트|페이지)|홈페이지|사이트|(?:상세|상품|소개)\s*페이지|(?:AI|인공지능)\s*에이전트|챗\s*봇|"
    r"(?<![가-힣])봇|\b(?:AI\s*agent|chat\s*bot|chatbot|bot|web\s*site|website|webpage|React|"
    r"HTML(?:\s*[/+·,]?\s*CSS)?|CSS)\b)"
)
_SOFTWARE_DELIVERY_REQUEST = re.compile(
    _SOFTWARE_TARGET
    + r"(?:\s*(?:을|를|의|코드|오류|버그|화면|레이아웃|기능|디자인|반응형|좀|code|ui|layout|component|bug)){0,5}\s*"
    r"(?:(?:제작|개발|구축|수정|리뉴얼|유지\s*보수)\s*(?:을|를|좀)?\s*"
    r"(?:의뢰|외주|요청|부탁|해\s*주실|해\s*주세요|도와\s*(?:주실|주세요))"
    r"|(?:만들어|제작해|개발해|수정해)\s*(?:주실|줄|주세요)"
    r"|(?:development|build|creation|fix|repair|redesign)\s+(?:request|needed|help))"
    r"|^\s*(?:please\s+)?(?:build|create|develop|fix|repair)\s+(?:an?\s+)?"
    + _SOFTWARE_TARGET,
    re.I,
)
_PHYSICAL_WORK_TITLE = re.compile(
    r"팬케이크|반찬|도시락|요리|음식|아이들?\s*(?:저녁|식사)|목공|가구|용달|짐\s*옮기|물류|포장|배송"
    r"|(?:입주|이사|가정집|사무실|매장|건물)\s*청소|청소\s*(?:용역|대행|외주)"
)
_DEVELOPMENT_TECHNOLOGY = re.compile(
    r"(?<![A-Za-z0-9])(?:React|Vue|Angular|Next\.?js|Node\.?js|TypeScript|JavaScript|Python|HTML|CSS)(?![A-Za-z0-9])",
    re.I,
)
_SOFTWARE_OBJECT = re.compile(
    r"코드|화면|컴포넌트|프론트엔드|백엔드|웹\s*(?:페이지|앱)|\b(?:UI|API|component|screen|page|code|script)\b",
    re.I,
)


def _discovery_keywords(query: str | None) -> list[str]:
    """Keep the persisted v1 flat-list query format readable on resume."""
    try:
        raw = json.loads(query or "[]")
    except (TypeError, ValueError) as exc:
        raise ParseError("저장된 탐색 검색어 형식을 읽을 수 없습니다") from exc
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise ParseError("저장된 탐색 검색어 형식이 목록이 아닙니다")
    normalized = {re.sub(r"\s+", "", value).casefold() for value in raw if value.strip()}
    expanded = set(normalized)
    for aliases in _TITLE_ALIASES:
        if normalized.intersection(aliases):
            expanded.update(aliases)
    return sorted(expanded)


class _Document(HTMLParser):
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.titles: list[str] = []
        self.bodies: list[str] = []
        self.addresses: list[str] = []
        self._capture: tuple[str, list[str]] | None = None
        self._script: list[str] | None = None
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self._script = []
        elif tag == "h1" or tag == "address" or (tag == "p" and "whitespace-pre-wrap" in (attrs.get("class") or "").split()):
            self._capture = (tag, [])
        elif tag == "br" and self._capture:
            self._capture[1].append("\n")

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._capture:
            self._capture[1].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None
        elif self._capture and tag == self._capture[0]:
            dest = {"h1": self.titles, "p": self.bodies, "address": self.addresses}[tag]
            dest.append(normalize_text("".join(self._capture[1])))
            self._capture = None

    def store(self) -> dict:
        for script in self.scripts:
            match = re.fullmatch(r"\s*window\.__RELAY_STORE__\s*=\s*(.+?)\s*;?\s*", script, re.S)
            if not match:
                continue
            try:
                value = json.loads(match[1].removesuffix(";"))
                data = json.loads(value) if isinstance(value, str) else None
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("client:root"), dict):
                return data
        raise ParseError("공개 HTML의 공고 데이터를 읽을 수 없습니다 (구조 변경 가능성)")


def _node(store: dict, pointer: object) -> dict:
    if isinstance(pointer, dict):
        value = store.get(pointer.get("__ref"))
        if isinstance(value, dict):
            return value
    raise ParseError("공개 HTML의 공고 참조가 누락되었습니다")


def _sitemap(fetcher: Fetcher, url: str, root_name: str):
    result = fetcher.get(url, max_bytes=50 * 1024 * 1024)
    if result.outcome != "ok":
        raise TaskFailed(result.outcome, f"사이트맵 요청 실패 (HTTP {result.status or '-'}, {result.outcome})")
    try:
        root = ET.fromstring(result.text or "")
    except ET.ParseError as exc:
        raise ParseError("공개 사이트맵 XML을 읽을 수 없습니다") from exc
    if root.tag != "{http://www.sitemaps.org/schemas/sitemap/0.9}" + root_name:
        raise ParseError("공개 사이트맵의 문서 형식이 변경되었습니다")
    return root


def _title_match(title: str, keywords: list[str], specific_keywords: list[str], title_pattern: re.Pattern) -> tuple[bool, bool, bool]:
    """(core, explore, physical_filtered) for one public listing title."""
    normalized = re.sub(r"\s+", "", title).casefold()
    query_match = any(word in normalized for word in keywords)
    core = bool(title_pattern.search(title) or _SOFTWARE_DELIVERY_REQUEST.search(title)) and query_match
    explore = not core and query_match and (
        any(word in normalized for word in specific_keywords) or bool(_WEAK_CREATION_REQUEST.search(title))
    )
    # Preserve all existing core candidates. Suppress only clear
    # physical-service requests entering the optional detail queue.
    technical_object = _DEVELOPMENT_TECHNOLOGY.search(title) and _SOFTWARE_OBJECT.search(title)
    if explore and _PHYSICAL_WORK_TITLE.search(title) and not technical_object:
        return core, False, True
    return core, explore, False


def _post_slug(title: str) -> str:
    """Public detail slug; matched all 782 installed detail URLs on 2026-10-17."""
    return re.sub(r"[^0-9a-z가-힣]+", "-", title.lower()).strip("-")


class DaangnAdapter(ProfiledSiteAdapter):
    FEED_PREFIX = "feed-"

    def _anchors(self) -> list[tuple[int, str, str]]:
        return [(int(region), label, tier) for region, label, tier in self.profile["discovery"]["feed"]["anchors"]]

    @property
    def max_tasks_per_run(self) -> int:
        return len(self._anchors()) + 1  # every due feed and the sitemap fit one watch

    def daily_list_requests(self, interval_minutes: int) -> int:
        """List requests per day at this watch interval (feeds + sitemap backstop)."""
        config = self.profile["discovery"]
        tiers = config["feed"]["tier_minutes"]
        anchors = self._anchors()
        # The next watch starts an interval after this one finishes, so the
        # watch period includes the paced feed requests themselves.
        per_watch = sum(min(1, interval_minutes / tiers[tier]) if tiers[tier] else 1 for _, _, tier in anchors)
        period = interval_minutes + per_watch * (self.min_interval_seconds + 1) / 60
        feeds = sum(1440 / max(period, tiers[tier]) for _, _, tier in anchors)
        sitemap = 1440 / max(interval_minutes, int(config["sitemap_min_revisit_minutes"])) * 2  # index + one map
        return math.ceil(feeds + sitemap)

    def plan_discovery(self, query_groups: list[dict], now: datetime):
        check = check_profile(self.profile)
        if not check.ready:
            raise SourceNotReady("research_incomplete", ", ".join(check.missing))
        keywords = list(dict.fromkeys(query for _, query in self._queries(query_groups)))
        if not keywords:
            raise SourceNotReady("no_queries", "활성화된 검색어가 없습니다")
        query = json.dumps(keywords, ensure_ascii=False)
        config = self.profile["discovery"]
        tiers = config["feed"]["tier_minutes"]
        feeds = [TaskSpec("national-title-v1", f"{self.FEED_PREFIX}{region}", f"{label} 지역 피드", query,
                          min_revisit_minutes=int(tiers[tier])) for region, label, tier in self._anchors()]
        sitemap = TaskSpec("national-title-v1", "nationwide-sitemap", "전국 공개 공고 목록", query,
                           min_revisit_minutes=int(config["sitemap_min_revisit_minutes"]))
        return DiscoveryPlan(
            [sitemap] + feeds,
            "전국 지역 피드·공개 공고 목록", "not_applicable", "지역 피드·공고 목록", 1,
            [f"전국 {len(feeds)}개 앵커 동네의 공개 지역 피드(기타·가벼운 업무·사무보조·과외 직종, 최근 20건)를 밀집도에 따라 매 감시(약 20~25분)~2시간 간격으로 확인합니다.",
             "피드 제목에서 활성 검색어와 명시 동의어로 제작 후보를 고르고, 건당 보수를 먼저, 월급·연봉 공고는 약한 후보로 낮추거나 빼고 상세 본문을 확인합니다.",
             "구체 검색어·약한 제작 요청 후보는 예산 안에서 제한된 수만 순환 확인합니다. 전체 공고의 본문을 검색하지 않습니다.",
             "공개 공고 사이트맵은 하루 두 번 누락 보완용으로 확인합니다. 피드가 덮지 못한 지역과 제목에 관련 표현이 없는 공고는 누락될 수 있고 전체 시장 포괄률은 알 수 없습니다."],
            None, None,
            query_labels={"national-title-v1": "웹사이트·프로그램·자동화 관련 공고"},
        )

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        if task.region_scope.startswith(self.FEED_PREFIX) and not task.cursor:
            return self._discover_feed(task, fetcher)
        if task.region_scope != "nationwide-sitemap" or task.cursor:
            raise ParseError("현재 어댑터와 맞지 않는 탐색 계획입니다. 새 탐색을 시작하세요.")
        config = self.profile["discovery"]
        index = _sitemap(fetcher, config["index_url"], "sitemapindex")
        maps = []
        for entry in index:
            url = entry.findtext("{*}loc") or ""
            if re.fullmatch(r"https://jobs\.daangn\.com/sitemaps/job-posts-\d+\.xml", url):
                maps.append(url)
        maps = list(dict.fromkeys(maps))
        if not maps or len(maps) > int(config.get("max_sitemaps", 20)):
            raise ParseError("공개 공고 사이트맵 목록이 없거나 확인한 처리 범위를 초과했습니다")
        keywords = _discovery_keywords(task.query)
        specific_keywords = [word for word in keywords if len(word) >= 2 and word not in _GENERIC_QUERY_TERMS]
        title_pattern = re.compile(config["title_pattern"], re.I)
        candidates = {}
        listed = {}
        invalid_urls = 0
        non_software_exploration_filtered = 0
        for sitemap in maps:
            for entry in _sitemap(fetcher, sitemap, "urlset"):
                url = (entry.findtext("{*}loc") or "").strip()
                if not self._link_re or not self._link_re.fullmatch(url):
                    invalid_urls += 1
                    continue
                post_id = self._post_id(url)
                # Do not match an accidental keyword (e.g. VBA) in the ID.
                segment = unquote(urlsplit(url).path.rsplit("/", 1)[1])
                slug = segment.rsplit("-", 1)[0] if segment != post_id else ""
                title = slug.replace("-", " ")
                modified, _ = parse_iso(entry.findtext("{*}lastmod"))
                ref = PostRef(url, post_id, title, modified)
                if post_id not in listed or (modified.timestamp() if modified else 0) > (
                        listed[post_id].source_updated_at.timestamp() if listed[post_id].source_updated_at else 0):
                    listed[post_id] = ref
                core, explore, physical = _title_match(title, keywords, specific_keywords, title_pattern)
                non_software_exploration_filtered += physical
                if core or explore:
                    candidate = (modified.timestamp() if modified else 0, ref, core)
                    if post_id not in candidates or (candidate[0], core) > (candidates[post_id][0], candidates[post_id][2]):
                        candidates[post_id] = candidate
        if invalid_urls and not listed:
            raise ParseError("사이트맵에 예상과 다른 공고 주소만 있습니다")
        ordered = sorted(candidates.values(), key=lambda x: (-x[0], x[1].url))
        refs = [ref for _, ref, core in ordered if core]
        exploration_refs = [ref for _, ref, core in ordered if not core]
        return DiscoverResult(refs, None, {
            "scope": "nationwide_public_sitemaps", "sitemaps": len(maps),
            "listed_urls": len(listed), "title_candidates": len(refs),
            "core_candidates": len(refs), "exploration_candidates": len(exploration_refs),
            "non_software_exploration_filtered": non_software_exploration_filtered,
            "invalid_urls": invalid_urls,
            "filter_basis": "제목의 제작 관련 표현 + 활성 검색어·명시 동의어, 이후 상세 본문 확인",
            "exploration_filter_basis": "활성 구체 검색어·약한 제작 요청 제목을 제한 순환 확인. 전체 본문 검색 아님",
        }, exploration_refs, list(listed.values()))

    def _discover_feed(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        """First page (20 newest) of one public region feed; no GraphQL paging."""
        region = int(task.region_scope.removeprefix(self.FEED_PREFIX))
        config = self.profile["discovery"]
        job_tasks = json.dumps(config["feed"]["job_tasks"], separators=(",", ":"))
        result = fetcher.get(f"{config['feed']['url']}?{urlencode({'regionId': region, 'jobTasks': job_tasks})}")
        if result.outcome != "ok":
            raise TaskFailed(result.outcome, f"지역 피드 요청 실패 (HTTP {result.status or '-'}, {result.outcome})")
        store = _Document(result.text or "").store()
        root = store["client:root"]
        # The web client fixes regionRange; accept only this region's feed
        # with exactly the requested job-task filter and no search keyword.
        keys = [key for key in root if key.startswith("searchedFeed(") and f'"region":{region},' in key
                and f'filter:{{"jobTasks":{job_tasks}}}' in key and '"searchKeyword":""' in key]
        if len(keys) != 1:
            raise ParseError("지역 피드 데이터를 찾을 수 없습니다 (구조 변경 또는 잘못된 지역)")
        edges = _node(store, root[keys[0]]).get("edges")
        if not isinstance(edges, dict) or not isinstance(edges.get("__refs"), list):
            raise ParseError("지역 피드 목록 형식이 변경되었습니다")
        keywords = _discovery_keywords(task.query)
        specific_keywords = [word for word in keywords if len(word) >= 2 and word not in _GENERIC_QUERY_TERMS]
        title_pattern = re.compile(config["title_pattern"], re.I)
        listed, refs, exploration_refs, published = [], [], [], []
        physical_filtered = employment_filtered = employment_demoted = closed = 0
        for edge_key in edges["__refs"]:
            card = _node(store, (store.get(edge_key) or {}).get("node"))
            post = _node(store, card.get("jobPost"))
            if post.get("__typename") != "JobPost":
                continue
            public_id = _node(store, post.get("daangnPermalink")).get("publicId")
            title = normalize_text(str(post.get("title") or ""))
            url = f"https://jobs.daangn.com/job-posts/{_post_slug(title)}-{public_id}"
            if not isinstance(public_id, str) or not self._link_re or not self._link_re.fullmatch(url):
                raise ParseError("지역 피드의 공고 주소 형식이 변경되었습니다")
            at, _ = parse_iso(post.get("publishedAt"))
            if at:
                published.append(at)
            salary = post.get("salaryType")
            ref = PostRef(url, public_id, title, at, priority=1 if salary == "PER_CASE" else 0)
            listed.append(ref)
            if post.get("closed") is True:
                closed += 1
                continue
            core, explore, physical = _title_match(title, keywords, specific_keywords, title_pattern)
            physical_filtered += physical
            # Every reviewed lead so far was paid per case. A monthly or yearly
            # salary is employment: a core title becomes optional, a weak one drops.
            if salary in ("MONTHLY", "YEARLY") and (core or explore):
                employment_filtered += explore
                employment_demoted += core
                core, explore = False, core
            (refs if core else exploration_refs if explore else []).append(ref)
        return DiscoverResult(refs, None, {
            "scope": "region_feed", "region_id": region, "listed_urls": len(listed),
            "oldest_published_at": min(published).isoformat() if published else None,
            "newest_published_at": max(published).isoformat() if published else None,
            "core_candidates": len(refs), "exploration_candidates": len(exploration_refs),
            "closed_listings": closed, "non_software_exploration_filtered": physical_filtered,
            "employment_exploration_filtered": employment_filtered, "employment_core_demoted": employment_demoted,
            "filter_basis": "지역 피드 제목의 제작 관련 표현 + 활성 검색어·명시 동의어, 건당 보수 우선, 이후 상세 본문 확인",
        }, exploration_refs, listed)

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost:
        if result.outcome != "ok" or urlsplit(result.final_url).hostname not in self.allowed_hosts:
            raise ParseError("정상 상세 응답이 아닙니다")
        public_id = self._post_id(result.final_url)
        if not public_id or (ref.source_post_id and public_id != ref.source_post_id):
            raise ParseError("요청한 공고와 상세 주소가 일치하지 않습니다")
        document = _Document(result.text or "")
        store = document.store()
        root_key = f'permalinkPairByPublicId(publicId:"{public_id}")'
        pair = _node(store, store["client:root"].get(root_key))
        permalink = _node(store, pair.get("daangnPermalink"))
        if permalink.get("publicId") != public_id:
            raise ParseError("상세 데이터의 공고 ID가 일치하지 않습니다")
        post = _node(store, permalink.get("model"))
        if post.get("__typename") != "JobPost":
            raise ParseError("구인 공고가 아닙니다")
        if post.get("hidden") or post.get("deleted"):
            raise PostNotCollectable("hidden_or_deleted", "숨김·삭제 상태인 공고는 본문을 저장하지 않습니다")
        user = _node(store, post.get("user"))
        if user.get("webCrawlNotAllowed") is True:
            raise PostNotCollectable("collection_disallowed", "공개 수집을 허용하지 않는 공고는 본문을 저장하지 않습니다")
        if user.get("webCrawlNotAllowed") is not False:
            raise PostNotCollectable("collection_permission_unknown", "공고의 공개 수집 허용 상태를 확인하지 못했습니다")
        masked_title = normalize_text(str(post.get("title(masking:true)") or ""))
        masked_body = normalize_text(str(post.get("content(masking:true)") or ""))
        # Reviews use the same paragraph class. Bind only the current post's
        # visible masked body, never a review or another recommended job.
        if len(document.titles) != 1 or masked_body not in document.bodies:
            raise ParseError("상세 제목·본문의 구조가 변경되었습니다")
        title, body = document.titles[0], masked_body
        # Closed posts render a visible "마감 " badge inside h1. Only remove
        # that exact badge when the matching post explicitly reports closed.
        if post.get("closed") is True and title == f"마감 {masked_title}":
            title = masked_title
        if not title or not body or title != masked_title:
            raise ParseError("공개 본문과 공고 데이터가 일치하지 않습니다")
        page = collect(result.text or "")
        published_at, precision = parse_iso(post.get("publishedAt"))
        status = "closed" if post.get("closed") is True else "open" if post.get("closed") is False else None
        evidence = [Ev(f"closed={str(post['closed']).lower()}", None, None, "explicit", "high", "공개 상세 페이지의 모집 상태")] if status else []
        return ParsedPost(
            title=title, body=body, original_url=result.final_url,
            canonical_url=canonicalize(result.final_url), source_post_id=public_id,
            workplace_raw=next(iter(document.addresses), None),
            published=DateInfo(published_at, precision, post.get("publishedAt")),
            pay=parse_pay(page.meta.get("karrot:embed_view_type:title", "")),
            source_status=status, status_evidence=evidence,
            contact_channel=self.profile["detail"]["contact_channel_label"],
            parse_notes=["공개 HTML의 표시 본문·보수·게시일·모집 상태 확인. 지원 조건과 재택 여부는 본문 근거로 판단."],
        )
