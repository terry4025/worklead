"""규칙 기반 분류. 각 축을 독립적으로 판정하고 원문 근거를 남긴다.

판정 원칙 (docs/REQUIREMENTS.md §8)
- '만들어드립니다' 는 판매자 홍보, '만들어주실 분 구합니다' 는 구매 수요.
- '엑셀 가능한 사무직' 은 VBA 개발 의뢰가 아니다.
- '온라인 쇼핑몰 직원' 은 재택이 아니다.
- '재택 가능' + '첫날 방문' → work_mode 는 재택이지만 collaboration_mode 는 onsite_required.
- '프리랜서' 만으로 완전 재택을 확정하지 않는다.
- 근무지 주소만으로 출근 필수로 확정하지 않는다.
- 근거가 없으면 unknown.
"""

from __future__ import annotations

import re

from .text import any_match, c, find
from .types import Ev, Judgement, Risk

RULE_VERSION = "rules-2026.09.6"

# ── 요청 유형 ─────────────────────────────────────────────────────────
SELLER = c(
    r"(?:만들어|제작해|개발해|구축해|제작\s*해|디자인해|대행해)\s*드(?:립니다|려요|림|릴게요)",
    r"제작\s*(?:대행|해드림)",
    r"외주\s*(?:받습니다|받아요|환영합니다)",
    r"(?:제작|개발)\s*(?:문의\s*주세요|상담\s*환영)",
    r"(?:제작|개발|창업)[^.\n]{0,10}도와\s*드(?:립니다|려요)",
)
JOB_SEEKER = c(r"구직\s*(?:중|합니다)", r"일\s*(?:구합니다|구해요|찾습니다|찾아요)", r"일자리\s*(?:구|찾)", r"알바\s*구해요")
BUYER = c(
    r"(?:만들어|제작해|개발해|수정해|구축해|연동해|리뉴얼\s*해|고쳐|맡아)\s*(?:주실|줄|주세요|주시|주셨으면)",
    r"(?:만들어|제작해|개발해)\s*주실\s*수\s*있는\s*분",
    r"(?:제작|개발|구축|수정|리뉴얼)\s*(?:을|를)?\s*(?:의뢰|외주)",
    r"(?:의뢰|외주)\s*(?:합니다|드립니다|드려요|맡기|하려|를\s*맡|주려)",
    r"(?:외주|의뢰)\s*(?:맡기려고|드리려고)",
    r"견적\s*(?:부탁|요청|보내|주세요|받고)",
    r"(?:만들|제작하|개발하)고\s*싶(?:어요|습니다)",
)
ONGOING = c(r"유지\s*보수", r"월\s*\d+\s*만\s*원?\s*고정", r"매달|매월|월\s*단위|정기적으로|장기\s*(?:계약|협업)|재계약")
EMPLOYMENT_STRONG = c(r"정규직", r"채용합니다", r"4대\s*보험", r"상근", r"주\s*5\s*일", r"연봉", r"사원\s*모집", r"직원\s*(?:모집|구합니다|채용)")
EMPLOYMENT_WEAK = c(r"시급", r"하루\s*\d+\s*시간", r"주\s*\d\s*일\s*근무", r"월급", r"근무\s*시간", r"아르바이트|알바|단기\s*(?:알바|근무)")
#: 건당·단기 작업 신호 — 고용 형식으로 올라왔지만 짧게 끝나는 개발·자동화 일 (크몽·숨고 밖의 틈새 수요)
SHORT_GIG = c(
    r"건\s*당|건\s*별",
    r"단기",
    r"하루\s*(?:만|알바|작업)|일일\s*알바|1\s*일\s*(?:작업|알바)",
    r"총\s*(?:[1-9]|[12]\d|30)\s*일",
    r"(?:1|2|3)\s*개월\s*(?:이내|이하|~)|1\s*주일?\s*(?:이내|이하|~)|한\s*달",
    r"프리랜서|외주|도급",
    r"재택|원격|비대면|온라인\s*(?:근무|작업|진행)",
)
#: 교육생·수강생 모집 광고 (일거리가 아님)
TRAINEE_AD = c(r"국비\s*지원", r"교육생\s*모집", r"수강생\s*모집", r"훈련생\s*모집", r"무료\s*(?:교육|숙식)")
#: 교육·과외 요청 (제작 의뢰와 구분해 확인 필요로 표시)
TEACHING = c(r"과외", r"가르쳐\s*(?:주실|줄|주세요|주시|드릴)", r"알려\s*(?:주실|줄\s*분|주세요)", r"배우고\s*싶", r"(?:교육|강의|수업|코칭)\s*(?:해\s*)?(?:주실|해주실|가능하신|구해)", r"선생님")
AMBIGUOUS_PERSON = c(r"(?:개발자|프리랜서|디자이너|작업자)\s*(?:구합니다|구해요|모집|찾습니다)")

# ── 계약 형태 ─────────────────────────────────────────────────────────
FULL_TIME = c(r"정규직", r"상근", r"4대\s*보험", r"연봉")
PART_TIME = c(r"시급[^.\n]{0,30}(?:하루|주\s*\d|시간)", r"(?:아르바이트|알바)", r"단기\s*(?:근무|알바)", r"하루\s*\d+\s*시간")
HOURLY_CONTRACT = c(r"시간\s*(?:당|단위)\s*(?:계약|정산)")
PROJECT = c(r"외주", r"의뢰", r"건\s*당", r"견적", r"프로젝트", r"납품")

# ── 근무 방식 ─────────────────────────────────────────────────────────
REMOTE_NEG = c(r"재택\s*(?:불가|안\s*됩니다|은\s*어렵)", r"원격\s*(?:불가|근무\s*불가)")
ONSITE = c(r"(?:사무실|매장|회사|현장)\s*(?:으로|로|에)\s*(?:오셔서|나오셔서|나와서|출근)", r"주\s*\d\s*일\s*출근", r"(?<!재택\s)출근(?!\s*(?:없이|안|불필요|하지))\s*(?:근무|필수|해야|하셔야)?", r"사무실\s*(?:근무|상주)", r"상주\s*(?:근무|개발)?", r"매장\s*근무")
HYBRID = c(r"재택\s*(?:병행|혼합)", r"주\s*\d\s*일\s*재택", r"일부\s*재택", r"하이브리드")
REMOTE_NEGOTIABLE = c(r"재택\s*(?:여부\s*)?협의", r"원격\s*(?:근무\s*)?협의", r"근무\s*(?:형태|방식)\s*협의")
REMOTE_EXPLICIT = c(
    r"완전\s*재택",
    r"전\s*과정\s*(?:원격|비대면|온라인)",
    r"재택\s*(?:근무|작업|진행|가능|으로|환영|OK|ok)",
    r"원격\s*(?:근무|작업|진행|으로|가능)",
    r"비대면\s*(?:진행|작업|으로)?",
    r"온라인\s*(?:으로|로)\s*(?:만\s*)?(?:진행|작업|소통)",
    r"리모트|remote",
)
REMOTE_WEAK = c(r"(?:메일|이메일|카톡|카카오톡)(?:로|으로)?\s*(?:전달|주고받|보내|드려)", r"파일로\s*주고받", r"화상\s*(?:미팅|회의)")
FREELANCE_ONLY = c(r"프리랜서")

# ── 진행 방식 ─────────────────────────────────────────────────────────
ONSITE_REQUIRED = c(
    r"(?:첫\s*(?:날|미팅|만남)|최초|초기)[^.\n]{0,25}(?:방문|대면|사무실|매장|학원|내방)",
    r"방문\s*(?:교육|미팅|회의)",
    r"대면\s*(?:미팅|교육|회의|으로)",
    r"(?:월|주)\s*\d\s*회[^.\n]{0,15}(?:회의|미팅|방문|출근)",
    r"회의\s*참석",
    r"출근(?!\s*(?:없이|안|불필요|하지))",
)
ONLINE_ONLY_EXPLICIT = c(
    r"미팅\s*없이",
    r"비대면",
    r"온라인\s*(?:으로|로)\s*만",
    r"전\s*과정\s*(?:원격|비대면|온라인)",
    r"완전\s*재택",
    r"대면\s*(?:없이|불필요)",
)
ONLINE_ONLY_WEAK = c(
    r"(?:메일|이메일|카톡|카카오톡|슬랙|slack|디스코드)(?:로|으로|과|와)?[^.\n]{0,12}(?:소통|전달|주고받|드려|합니다)",
    r"화면\s*공유",
    r"화상\s*(?:미팅|회의)",
    r"파일로\s*주고받",
    r"원격으로\s*진행",
    r"온라인\s*(?:으로|로)\s*진행",
)
COLLAB_NEGOTIABLE = c(r"(?:미팅|대면)\s*(?:여부\s*)?(?:협의|조율)")

# ── 지원 지역 ─────────────────────────────────────────────────────────
NATIONWIDE = c(r"지역\s*(?:상관|무관|제한\s*없)", r"전국\s*(?:어디|누구|가능|지원)", r"거주지\s*(?:무관|상관)", r"어디서나")
REGIONAL = c(r"[가-힣]{2,6}\s*(?:거주자|거주하시는\s*분|지역\s*분)\s*만", r"(?:인근|근처)\s*(?:거주자|분)\s*만", r"출퇴근\s*가능한\s*분만?")

# ── 업무 카테고리 ─────────────────────────────────────────────────────
#: 소프트웨어 자동화만. '자동화물(자동+화물)'·물류/설비/공장 자동화는 제외 (2026-09 실제 공고에서 오탐 확인)
AUTOMATION_WORD = r"(?<!물류)(?<!설비)(?<!공장)자동화(?!물|\s*(?:장비|설비|라인|레일|기계|품질|안전))"
DEV_VERB = re.compile(r"제작|개발|구축|수정|리뉴얼|유지\s*보수|연동|" + AUTOMATION_WORD + r"|만들|코딩|프로그래밍|매크로|스크립트")
CATEGORY_RULES: list[tuple[str, list[re.Pattern[str]], bool]] = [
    # (id, patterns, 개발 동사가 없어도 개발 업무로 보는지)
    ("landing", c(r"랜딩\s*페이지", r"원\s*페이지|1\s*페이지", r"이벤트\s*페이지"), True),
    ("shop", c(r"쇼핑몰", r"카페24|자사몰", r"상세\s*페이지"), False),
    ("website", c(r"홈페이지", r"웹\s*사이트|웹사이트", r"웹\s*(?:개발|코딩|퍼블리싱)|HTML\s*코딩|퍼블리싱", r"사이트\s*(?:제작|구축|개발|리뉴얼)", r"워드프레스|wordpress", r"예약\s*페이지", r"웹\s*페이지"), False),
    ("fullstack", c(r"웹\s*(?:프로그램|서비스|앱)", r"관리자\s*페이지|관리\s*페이지", r"풀스택", r"백엔드|서버\s*개발", r"앱\s*[·/,]\s*웹|웹\s*[·/,]\s*앱", r"플랫폼\s*개발"), True),
    ("software", c(r"프로그램\s*(?:제작|개발|이\s*필요|만들)", r"소프트웨어", r"C#|파이썬|python|자바|윈도우\s*(?:PC|프로그램)", r"변환\s*프로그램", r"앱\s*(?:개발|제작)", r"어플(?:리케이션)?\s*(?:개발|제작)", r"모바일\s*앱", r"크로스\s*플랫폼", r"플러터|flutter|리액트\s*네이티브"), True),
    ("vba", c(r"VBA|vba", r"매크로", r"엑셀[^.\n]{0,15}(?:자동|매크로|함수\s*개발)"), True),
    ("automation", c(AUTOMATION_WORD, r"자동으로", r"API|api", r"연동", r"알림톡", r"구글\s*(?:시트|스프레드시트)", r"스프레드시트"), True),
    ("data", c(r"데이터\s*(?:정리|입력|취합)", r"정리\s*작업", r"엑셀\s*(?:정리|취합|입력)", r"취합"), False),
]
DEV_CATEGORIES = {"landing", "shop", "website", "fullstack", "software", "vba", "automation"}

# ── 위험 신호 (작성자를 단정하지 않고 원문 근거만 표시) ───────────────────
RISK_RULES: list[tuple[str, str, list[re.Pattern[str]]]] = [
    (
        "upfront_payment",
        "구직자에게 선입금 요구",
        c(r"(?:교육비|자료비|교육\s*자료비|가입비|등록비|보증금|키트\s*비용)[^.\n]{0,12}(?:입금|결제|선납|납부|송금)", r"선입금\s*(?:후|하시면|필수)"),
    ),
    ("income_guarantee", "과장된 고수익 보장 표현", c(r"(?:고수익|월\s*\d+\s*(?:만|천)?\s*원?\s*이상)[^.\n]{0,10}보장", r"수익\s*(?:100%\s*)?보장")),
    ("account_rental", "계정·명의 대여 요구", c(r"(?:계정|아이디|명의|통장)\s*(?:대여|빌려|양도|임대)")),
    ("signup_lure", "가입·설치 유도", c(r"(?:가입|앱\s*설치|회원\s*등록)\s*(?:후|하시면|필수)[^.\n]{0,20}(?:수익|지급|포인트|적립)")),
    ("illegal_work", "불법 소지 작업 (크랙·불법 복제·리뷰 조작 등)", c(r"크랙", r"불법\s*복제", r"정품\s*인증\s*우회", r"(?:리뷰|후기|평점)\s*조작")),
    ("personal_info", "부적절한 개인정보 요구", c(r"(?:주민\s*등록\s*번호|주민번호|통장\s*사본|신분증)[^.\n]{0,10}(?:보내|제출|사진|전송)")),
]
# 개발 계약의 착수금(발주자 → 개발자)은 위험 신호가 아니다
DOWN_PAYMENT_OK = re.compile(r"착수금|계약금|선금\s*\d+\s*%")


def _j(field: str, value: str, ev: Ev | None, basis_if_none: str | None = None) -> Judgement:
    if ev is None:
        return Judgement(field, value, basis_if_none)  # type: ignore[arg-type]
    return Judgement(field, value, ev.basis, [ev])


def classify_intent(title: str, body: str) -> Judgement:
    seller = find(SELLER, body, title, "explicit", "제작 서비스를 판매하는 표현")
    if seller:
        return _j("demand_intent", "seller_service", seller)
    seeker = find(JOB_SEEKER, body, title, "explicit")
    if seeker:
        return _j("demand_intent", "job_seeker", seeker)

    strong = find(EMPLOYMENT_STRONG, body, title, "explicit")
    if strong:
        return _j("demand_intent", "employee_hiring", strong)

    buyer = find(BUYER, body, title, "explicit")
    weak_emp = find(EMPLOYMENT_WEAK, body, title, "inferred", "시급·근무시간 기재 — 단기 고용으로 판단")
    if buyer and weak_emp and weak_emp.quote and re.search(r"시급|하루\s*\d+\s*시간|근무\s*시간", weak_emp.quote):
        weak_emp.basis = "inferred"
        return Judgement("demand_intent", "employee_hiring", "inferred", [weak_emp, buyer])
    if buyer:
        ongoing = find(ONGOING, body, title, "explicit")
        if ongoing:
            return Judgement("demand_intent", "buyer_ongoing", "explicit", [buyer, ongoing])
        return _j("demand_intent", "buyer_project", buyer)
    if weak_emp:
        return _j("demand_intent", "employee_hiring", weak_emp)
    amb = find(AMBIGUOUS_PERSON, body, title, "inferred", "외주 의뢰인지 고용인지 구분할 근거 부족")
    if amb:
        return Judgement("demand_intent", "unknown", None, [amb])
    return Judgement("demand_intent", "unknown", None)


def classify_short_gig(title: str, body: str, intent: Judgement, engagement: Judgement, dev: bool) -> Judgement:
    """고용·미확인으로 판정된 글 중 개발·자동화 일이면서 건당·단기·재택 신호가 있으면 '건당·단기 작업 구인'으로 본다.
    정규직·4대보험 같은 강한 고용 신호가 있으면 바꾸지 않는다."""
    if intent.value not in ("employee_hiring", "unknown") or not dev or engagement.value == "full_time":
        return intent
    if find(EMPLOYMENT_STRONG, body, title, "explicit"):
        return intent
    signal = find(SHORT_GIG, body, title, "inferred", "프리랜서·건당·단기·재택 표현 — 짧게 끝나는 작업 구인으로 판단")
    if signal is None:
        return intent
    return Judgement("demand_intent", "short_gig", "inferred", [signal, *intent.evidence])


def classify_engagement(title: str, body: str, intent: str) -> Judgement:
    ft = find(FULL_TIME, body, title, "explicit")
    if ft:
        return _j("engagement_type", "full_time", ft)
    pt = find(PART_TIME, body, title, "explicit")
    if pt:
        return _j("engagement_type", "part_time", pt)
    hc = find(HOURLY_CONTRACT, body, title, "explicit")
    if hc:
        return _j("engagement_type", "hourly_contract", hc)
    pj = find(PROJECT, body, title, "explicit" if intent.startswith("buyer") else "inferred")
    if pj:
        return _j("engagement_type", "project", pj)
    if intent in ("buyer_project", "buyer_ongoing"):
        return Judgement("engagement_type", "project", "inferred")
    return Judgement("engagement_type", "unknown", None)


def classify_work_mode(title: str, body: str) -> Judgement:
    neg = find(REMOTE_NEG, body, title, "explicit")
    if neg:
        return _j("work_mode", "onsite", neg)
    hybrid = find(HYBRID, body, title, "explicit")
    if hybrid:
        return _j("work_mode", "hybrid", hybrid)
    nego = find(REMOTE_NEGOTIABLE, body, title, "explicit")
    remote = find(REMOTE_EXPLICIT, body, title, "explicit")
    onsite = find(ONSITE, body, title, "explicit")
    if remote and not onsite:
        return _j("work_mode", "fully_remote", remote)
    if onsite and not remote:
        return _j("work_mode", "onsite", onsite)
    if remote and onsite:
        # 예: '재택 근무, 월 1회 본사 회의 참석' → 재택 + 진행 방식에서 대면 필요로 표시
        if re.search(r"주\s*\d\s*일\s*출근|사무실\s*(?:근무|상주)", onsite.quote):
            return Judgement("work_mode", "hybrid", "explicit", [remote, onsite])
        return _j("work_mode", "fully_remote", remote)
    if nego:
        return _j("work_mode", "negotiable", nego)
    weak = find(REMOTE_WEAK, body, title, "inferred", "재택 명시 없음 — 자료 전달 방식으로만 추정")
    if weak:
        return _j("work_mode", "fully_remote", weak)
    fl = find(FREELANCE_ONLY, body, title, "inferred", "'프리랜서'만으로 완전 재택을 확정하지 않음")
    if fl:
        return Judgement("work_mode", "unknown", None, [fl])
    return Judgement("work_mode", "unknown", None)


def classify_collaboration(title: str, body: str, work_mode: str) -> Judgement:
    onsite = find(ONSITE_REQUIRED, body, title, "explicit")
    if onsite:
        return _j("collaboration_mode", "onsite_required", onsite)
    if work_mode == "onsite":
        return Judgement("collaboration_mode", "onsite_required", "inferred")
    exp = find(ONLINE_ONLY_EXPLICIT, body, title, "explicit")
    if exp:
        return _j("collaboration_mode", "online_only", exp)
    nego = find(COLLAB_NEGOTIABLE, body, title, "explicit")
    if nego:
        return _j("collaboration_mode", "negotiable", nego)
    weak = find(ONLINE_ONLY_WEAK, body, title, "inferred")
    if weak:
        return _j("collaboration_mode", "online_only", weak)
    return Judgement("collaboration_mode", "unknown", None)


def classify_scope(title: str, body: str) -> Judgement:
    reg = find(REGIONAL, body, title, "explicit")
    if reg:
        return _j("applicant_scope", "regional_restriction", reg)
    nat = find(NATIONWIDE, body, title, "explicit")
    if nat:
        return _j("applicant_scope", "nationwide", nat)
    return Judgement("applicant_scope", "unknown", None)


def classify_categories(title: str, body: str) -> tuple[list[str], bool]:
    text = f"{title}\n{body}"
    has_verb = bool(DEV_VERB.search(text))
    out: list[str] = []
    for cid, pats, inherently_dev in CATEGORY_RULES:
        if any_match(pats, text) and (inherently_dev or has_verb or cid == "data"):
            out.append(cid)
    # '엑셀 데이터 정리' 는 데이터 정리이지 VBA 개발이 아니다
    if "vba" in out and not re.search(r"VBA|vba|매크로|자동", text):
        out.remove("vba")
    dev = any(cid in DEV_CATEGORIES for cid in out) and has_verb
    if not out:
        out = ["other"]
    return out, dev


def detect_risks(body: str) -> list[Risk]:
    risks: list[Risk] = []
    for rid, label, pats in RISK_RULES:
        for p in pats:
            m = p.search(body)
            if not m:
                continue
            if rid == "upfront_payment" and DOWN_PAYMENT_OK.search(body[max(0, m.start() - 10) : m.end() + 10]):
                continue
            risks.append(Risk(rid, label, m.group(0), m.start(), m.end()))
            break
    return risks
