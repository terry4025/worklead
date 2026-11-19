"""Anonymous public HTML adapter, verified against the 2026-09-28 site.

Region feeds (/s?regionId=, first 20 only) were verified on 2026-10-17;
the web client fixes regionRange and pages only through GraphQL.
Since 2026-11-04 the relay records arrive in TanStack Start stream parts
instead of window.__RELAY_STORE__ (see serialized.py); the DOM is unchanged.

Only the downloaded document is parsed. Embedded data is decoded as data,
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
from .serialized import SerializedError, relay_records


# Explicit technical variants, not fuzzy matches or unrestricted expansion.
# Expansion starts from enabled query terms; an Excel-only query stays scoped.
_TITLE_ALIASES = (
    ("홈페이지", "홈피", "웹사이트", "웹페이지", "웹개발", "웹제작", "웹서비스", "사이트", "website", "webpage", "상세페이지", "상품페이지", "소개페이지", "html"),
    ("프로그램", "소프트웨어", "코딩", "아두이노", "arduino", "프로그래밍"),
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
# Explicit publishing/integration help is a deliverable even when no word
# "개발" appears. Do not infer these from bare app/technology/store mentions.
_SPECIALIZED_DELIVERY_REQUEST = re.compile(
    r"(?:(?:앱\s*스토어|플레이\s*스토어)(?:에)?\s*등록"
    r"|(?:구글\s*(?:스프레드시트|시트)|스프레드시트)(?:의|을|를)?\s*연동"
    r"|서버(?:의|을|를|에)?\s*배포)"
    r"\s*(?:을|를|좀)?\s*(?:의뢰|외주|요청|부탁|해\s*주실|해\s*주세요|도와\s*(?:주실|주세요))",
    re.I,
)
_SERVER_DEPLOYMENT_REQUEST = re.compile(
    r"서버(?:의|을|를|에)?\s*배포\s*(?:을|를|좀)?\s*"
    r"(?:의뢰|외주|요청|부탁|해\s*주실|해\s*주세요|도와\s*(?:주실|주세요))",
    re.I,
)
_NEGATED_REQUEST_SUFFIX = re.compile(
    r"\s*(?:(?:이|가|은|는|을|를|도)\s*)?"
    r"(?:아니|아닌|아닙|아님|없|불필요|대신|필요\s*없|"
    r"안\s*(?:하|해|합|한|할)|(?:하|해|드리|맡기|진행하|진행해)지\s*(?:않|마)|"
    r"(?:게|것(?:이|은)?)\s*(?:아니|아닌|아닙|아님)|"
    r"드리(?:는|려는)\s*(?:게|것(?:이|은)?)\s*(?:아니|아닌|아닙|아님))",
    re.I,
)


def _positive_requests(pattern: re.Pattern, title: str) -> list[re.Match]:
    """Keep each requested operation and its trailing negation in one clause."""
    return [match for match in pattern.finditer(title)
            if not _NEGATED_REQUEST_SUFFIX.match(title, match.end())]


_PHYSICAL_WORK_TITLE = re.compile(
    r"팬케이크|반찬|도시락|요리|음식|아이들?\s*(?:저녁|식사)|목공|가구|용달|짐\s*옮기|물류|포장|배송"
    r"|(?:입주|이사|가정집|사무실|매장|건물)\s*청소|청소\s*(?:용역|대행|외주)"
)
# Coding teachers/tutors are a large share of '코딩' titles; they are not delivery requests.
_TEACHING_TITLE = re.compile(r"강사|선생님|과외|레슨|수업|강의|튜터")
# Must stay in sync with the suffix added to profile.json discovery.title_pattern.
# Core matches from only these terms are dropped for teacher titles.
_NEW_CORE_TERMS = re.compile(r"구글\s*시트|스프레드\s*시트|아두이노|코딩\s*(?:해\s*주|도와|봐\s*주|요청)|프로그래밍|(?i:html)")
_DEVELOPMENT_TECHNOLOGY = re.compile(
    r"(?<![A-Za-z0-9])(?:React|Vue|Angular|Next\.?js|Node\.?js|TypeScript|JavaScript|Python|HTML|CSS)(?![A-Za-z0-9])",
    re.I,
)
_SOFTWARE_OBJECT = re.compile(
    r"코드|화면|컴포넌트|프론트엔드|백엔드|웹\s*(?:페이지|앱)|\b(?:UI|API|component|screen|page|code|script)\b",
    re.I,
)
# These titles are not useful probes of body-only software demand. Keep the
# audit narrower than a general job crawl; its results still pass normal body
# analysis and never become application candidates from their title alone.
_AUDIT_EMPLOYMENT_TITLE = re.compile(
    r"서빙|주방|설거지|배달|배송|택배|물류|포장|청소|가사|돌봄|간병|요양|경비|전단|캐셔"
    r"|생산직|공사현장|상하차|일용직|홀\s*(?:직원|알바)|매장\s*(?:직원|알바|관리)"
    r"|정규직|계약직|직원\s*(?:모집|구함|구합니다)|아르바이트|시급|월급|연봉"
    r"|고수익|계정\s*(?:대여|판매)|명의\s*(?:대여|판매)|리뷰\s*(?:작성|알바)|물품\s*제작|민원"
)
_AUDIT_REQUEST_TITLE = re.compile(
    r"도와\s*(?:주실|줄|주세요)|도움|전문가"
    r"|(?:간단한|작은|짧은)\s*(?:일|작업)|(?:작업|업무).{0,12}(?:부탁|요청|가능|구함|구해|구합니다|하실)"
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
        self.markup = markup
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
        try:
            return relay_records(self.markup)
        except SerializedError:
            pass
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
    query_match = any(word in normalized or (word == "앱" and any(alias in normalized for alias in ("어플리케이션", "애플리케이션", "어플"))) for word in keywords)
    software_requests = _positive_requests(_SOFTWARE_DELIVERY_REQUEST, title)
    software_delivery = bool(software_requests)
    specialized_delivery = bool(_positive_requests(_SPECIALIZED_DELIVERY_REQUEST, title))
    # A concrete React/HTML/CSS screen change is in an enabled website scope;
    # server deployment belongs to an enabled software scope. This does not
    # make an Excel-only query discover unrelated app/web jobs, and requires
    # the full object + change + request signal before a semantic match.
    web_scope = any(word in keywords for word in ("홈페이지", "웹사이트", "웹페이지", "웹개발", "웹제작"))
    software_scope = any(word in keywords for word in ("프로그램", "소프트웨어", "파이썬", "python"))
    # The technology and object must belong to the very operation requested.
    # A chatbot order followed by an unrelated React/code note is not a web
    # request and must not override a disabled chatbot query.
    web_change = any(_DEVELOPMENT_TECHNOLOGY.search(match.group()) and _SOFTWARE_OBJECT.search(match.group())
                     for match in software_requests)
    scoped_match = bool(web_scope and web_change or software_scope and _positive_requests(_SERVER_DEPLOYMENT_REQUEST, title))
    query_match = query_match or scoped_match
    core = bool(title_pattern.search(title) or software_delivery or specialized_delivery) and query_match
    if (core and _TEACHING_TITLE.search(title) and not software_delivery and not specialized_delivery
            and not title_pattern.search(_NEW_CORE_TERMS.sub(" ", title))):
        return False, False, True
    explore = not core and query_match and (
        any(word in normalized for word in specific_keywords) or bool(_WEAK_CREATION_REQUEST.search(title))
    )
    # Preserve all existing core candidates. Suppress only clear
    # physical-service requests entering the optional detail queue.
    technical_object = _DEVELOPMENT_TECHNOLOGY.search(title) and _SOFTWARE_OBJECT.search(title)
    if explore and _PHYSICAL_WORK_TITLE.search(title) and not technical_object:
        return core, False, True
    if explore and _TEACHING_TITLE.search(title) and not software_delivery:
        return core, False, True
    return core, explore, False


# Feed cards dropped salaryType on 2026-11-04; the visible label still says it.
_SALARY_LABELS = (("건당", "PER_CASE"), ("시급", "HOURLY"), ("일급", "DAILY"),
                  ("주급", "WEEKLY"), ("월급", "MONTHLY"), ("연봉", "YEARLY"))


def _salary_type(post: dict) -> str | None:
    if post.get("salaryType"):
        return post["salaryType"]
    label = normalize_text(str(post.get("stringifiedSalary") or ""))
    return next((kind for prefix, kind in _SALARY_LABELS if label.startswith(prefix)), None)


def _post_slug(title: str) -> str:
    """Public detail slug; matched all 782 installed detail URLs on 2026-10-17."""
    return re.sub(r"[^0-9a-z가-힣]+", "-", title.lower()).strip("-")


class DaangnAdapter(ProfiledSiteAdapter):
    FEED_PREFIX = "feed-"

    def _anchors(self) -> list[tuple[int, str, str]]:
        return [(int(region), label, tier) for region, label, tier in self.profile["discovery"]["feed"]["anchors"]]

    @property
    def max_tasks_per_run(self) -> int:
        return len(self._anchors()) + 2  # due feeds, sitemap and saved candidates

    def saved_candidate_kind(self, title: str, query: str | None):
        keywords = _discovery_keywords(query)
        specific = [word for word in keywords if len(word) >= 2 and word not in _GENERIC_QUERY_TERMS]
        core, explore, _ = _title_match(title, keywords, specific, re.compile(self.profile["discovery"]["title_pattern"], re.I))
        return "core" if core else "exploration" if explore else None

    def title_audit_candidate(self, title: str, query: str | None = None) -> bool:
        """Weak request worth a bounded body probe, never a positive match."""
        if query is not None:
            keywords = set(_discovery_keywords(query))
            # An explicitly narrow search (e.g. Excel only) must stay narrow.
            # Audit is a backstop for a broad software search, not an override
            # of disabled groups or a way to revive unselected title matches.
            if sum(bool(keywords.intersection(aliases)) for aliases in _TITLE_ALIASES) < 2:
                return False
        return (4 <= len(title) <= 200 and not _PHYSICAL_WORK_TITLE.search(title)
                and not _AUDIT_EMPLOYMENT_TITLE.search(title)
                and not re.search(self.profile["discovery"]["title_pattern"], title, re.I)
                and not re.search(_SOFTWARE_TARGET, title, re.I)
                and not _DEVELOPMENT_TECHNOLOGY.search(title)
                and bool(_AUDIT_REQUEST_TITLE.search(title) or _WEAK_CREATION_REQUEST.search(title)))

    def daily_list_requests(self, interval_minutes: int) -> int:
        """List requests per day at this watch interval (feeds + sitemap backstop)."""
        if self.profile["discovery"]["feed"].get("adaptive"):
            return self.remaining_list_requests(86400, interval_minutes, startup=False)
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

    def remaining_list_requests(self, seconds: float, interval_minutes: int, *, startup: bool = True) -> int:
        """Conservative paced-poll reserve including a bounded hot set.

        No assumed HTTP latency discount: retries and actual host limits remain
        enforced by Fetcher. One initial poll per unit protects startup/catch-up.
        """
        config = self.profile["discovery"]
        feed = config["feed"]
        adaptive = feed.get("adaptive")
        if not adaptive:
            return math.ceil(self.daily_list_requests(interval_minutes) * seconds / 86400)
        minutes = max(0, seconds) / 60
        periods = [max(interval_minutes, int(feed["tier_minutes"][tier])) for _, _, tier in self._anchors()]
        hot = max(interval_minutes, int(adaptive["hot_minutes"]))
        # The hot set may change over time. Reserve the highest possible
        # additional volume for its bounded size, independent of today's rank.
        base = sum(math.ceil(minutes / period) for period in periods)
        # A rotating hot set may overlap ordinary polls differently each day.
        # Reserve its entire rolling allowance rather than subtracting a
        # fixed set's base volume. The planner shares eight slots per 10 min.
        priority = min(len(periods), int(adaptive["max_hot_anchors"])) * math.ceil(minutes / hot)
        sitemap = math.ceil(minutes / max(interval_minutes, int(config["sitemap_min_revisit_minutes"]))) * (1 + int(config.get("reserved_sitemap_count", 1)))
        return base + priority + sitemap + (len(periods) + min(len(periods), int(adaptive["max_hot_anchors"])) + 2 if startup and seconds > 0 else 0)

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
            [f"전국 {len(feeds)}개 앵커 동네의 공개 지역 피드(기타·가벼운 업무·사무보조·과외 직종, 최근 20건)를 기본 30분~2시간 간격으로 확인하고, 5분마다 확인할 앵커가 있는지 점검합니다.",
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
        audit = {}
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
                elif self.profile["discovery"].get("title_audit_requests_per_watch", 0) and self.title_audit_candidate(title, task.query):
                    audit[post_id] = ref
        if invalid_urls and not listed:
            raise ParseError("사이트맵에 예상과 다른 공고 주소만 있습니다")
        ordered = sorted(candidates.values(), key=lambda x: (-x[0], x[1].url))
        refs = [ref for _, ref, core in ordered if core]
        exploration_refs = [ref for _, ref, core in ordered if not core]
        audit = {key: ref for key, ref in audit.items() if key not in candidates}
        exploration_refs.extend(audit.values())
        return DiscoverResult(refs, None, {
            "scope": "nationwide_public_sitemaps", "sitemaps": len(maps),
            "listed_urls": len(listed), "title_candidates": len(refs),
            "core_candidates": len(refs), "exploration_candidates": len(exploration_refs),
            "non_software_exploration_filtered": non_software_exploration_filtered,
            "invalid_urls": invalid_urls,
            "title_audit_ids": sorted(audit), "title_audit_candidates": len(audit),
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
        audit_ids = []
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
            salary = _salary_type(post)
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
            if (not core and not explore and salary == "PER_CASE"
                    and config.get("title_audit_requests_per_watch", 0)
                    and self.title_audit_candidate(title, task.query)):
                explore = True
                audit_ids.append(public_id)
            (refs if core else exploration_refs if explore else []).append(ref)
        return DiscoverResult(refs, None, {
            "scope": "region_feed", "region_id": region, "listed_urls": len(listed),
            "oldest_published_at": min(published).isoformat() if published else None,
            "newest_published_at": max(published).isoformat() if published else None,
            "core_candidates": len(refs), "exploration_candidates": len(exploration_refs),
            "closed_listings": closed, "non_software_exploration_filtered": physical_filtered,
            "employment_exploration_filtered": employment_filtered, "employment_core_demoted": employment_demoted,
            "title_audit_ids": audit_ids, "title_audit_candidates": len(audit_ids),
            "feed_saturated": len(listed) >= 20,
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
