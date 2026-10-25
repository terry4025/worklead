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
from .pay import EXPLICIT_SALARY
from .types import Ev, Judgement, Risk

RULE_VERSION = "rules-2026.10.25-fit2"

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
    r"(?:홈페이지|홈피|웹\s*(?:사이트|페이지|서비스)|워드프레스|프로그램|소프트웨어|앱|어플(?:리케이션)?|API|매크로)"
    r"(?:\s*(?:의|을|를|접속|오류|버그|결제|로그인|기능|페이지|서버|코드|좀|에러|이|가)){0,8}\s*"
    r"(?:고쳐\s*(?:주실|주세요)|(?:복구|버그\s*수정|오류\s*해결|연동|배포|퍼블리싱)\s*(?:을|를)?\s*(?:의뢰|요청|부탁|해\s*주실|해\s*주세요))"
    r"(?!\s*(?:는|은|가|이)?\s*(?:아니|아닌|아닙|아님|없))",
    r"(?:만들어|제작해|개발해|수정해|구축해|연동해|리뉴얼\s*해|고쳐|맡아)\s*(?:주실|줄|주세요|주시|주셨으면)",
    r"(?:만들어|제작해|개발해)\s*주실\s*수\s*있는\s*분",
    r"(?:제작|개발|구축|수정|리뉴얼)\s*(?:을|를)?\s*(?:의뢰|외주)",
    r"(?:의뢰|외주)\s*(?:합니다|드립니다|드려요|맡기|하려|를\s*맡|주려)",
    r"(?:외주|의뢰)\s*(?:맡기려고|드리려고)",
    r"견적\s*(?:부탁|요청|보내|주세요|받고)",
    r"(?:만들|제작하|개발하)고\s*싶(?:어요|습니다)",
    r"(?:제작|개발|구축|수정)(?:을|를|하는\s*것을|\.?등을)?\s*(?:좀\s*)?도와\s*(?:주실|주세요)",
    r"(?:제작|개발|구축|수정)\s*(?:요청|부탁)\s*(?:합니다|드립니다|드려요|부탁드립니다)?",
    r"(?:제작|개발|구축|수정)\s*(?:을|를)?\s*(?:해주실\s*)?프리랜서\s*(?:구합니다|구해요|모집|찾습니다)",
    # A named software deliverable and a person request are both required.
    # Generic skill/staff recruitment remains ambiguous or employment.
    r"(?:홈페이지|홈피|웹\s*사이트|웹\s*페이지|랜딩\s*페이지|쇼핑몰|자사몰|카페24|프로그램|소프트웨어|앱|어플|매크로)\s*(?:을|를)?\s*(?:제작|개발|구축|수정|리뉴얼)\s*(?:가능(?:하신|한)|할\s*수\s*있는|하실|해\s*주실)\s*(?:분|프리랜서)(?:을|를)?\s*(?:계실까요|있을까요|구합니다|구해요|찾습니다|찾아요|모집합니다)",
    r"(?:홈페이지|홈피|웹\s*사이트|웹\s*페이지|프로그램|소프트웨어|앱)\s*(?:제작|개발|구축|수정)\s*(?:에\s*)?(?:능숙하신|익숙하신|경험\s*있으신)\s*분\s*(?:좀\s*)?도와\s*(?:주실|주세요)",
    # A named digital deliverable + concrete change + help request. Generic
    # design/help, operating a shop, teaching and staff recruitment are not
    # promoted by these expressions; seller/employment precedence still wins.
    r"(?:홈페이지|홈피|웹\s*(?:사이트|페이지|앱)|웹\s*[&·/]\s*앱|프로그램|소프트웨어|앱|어플)"
    r"[^.\n]{0,80}?(?:리뉴얼|구현|패키징|오류\s*수정|편집|디자인\s*작업)"
    r"\s*(?:도|을|를|등을|하는\s*작업을|작업등을|작업을)?\s*(?:해\s*)?(?:도와\s*)?(?:주실|주세요)",
    r"(?:(?:웹\s*사이트|홈페이지)(?:을|를)?\s*앱으로|앱\s*스토어(?:에)?|플레이\s*스토어(?:에)?)\s*등록"
    r"\s*(?:을|를|하는\s*작업을|작업을)?\s*(?:해\s*)?(?:도와\s*)?(?:주실|주세요)",
)
ONGOING = c(r"유지\s*보수", r"월\s*\d+\s*만\s*원?\s*고정", r"매달|매월|월\s*단위|정기적으로|장기\s*(?:계약|협업)|재계약")
EMPLOYMENT_STRONG = [*c(r"정규직", r"채용합니다", r"4대\s*보험", r"상근", r"주\s*5\s*일", r"연봉", r"사원\s*모집", r"직원\s*(?:모집|구합니다|채용)"), EXPLICIT_SALARY]
EMPLOYMENT_WEAK = c(r"시급", r"하루\s*\d+\s*시간", r"주\s*\d\s*일\s*근무", r"월급", r"근무\s*시간", r"아르바이트|알바|단기\s*(?:알바|근무)")
AMBIGUOUS_PERSON = c(r"(?:개발자|프리랜서|디자이너|작업자)\s*(?:구합니다|구해요|모집|찾습니다)")
OPERATIONAL_STAFF = c(r"쇼핑몰\s*관리(?:를|을)?\s*하실\s*분(?:을)?\s*(?:구합니다|모십니다)")

# ── 계약 형태 ─────────────────────────────────────────────────────────
FULL_TIME = c(r"정규직", r"상근", r"4대\s*보험", r"연봉")
PART_TIME = c(r"시급[^.\n]{0,30}(?:하루|주\s*\d|시간)", r"(?:아르바이트|알바)", r"단기\s*(?:근무|알바)", r"하루\s*\d+\s*시간")
HOURLY_CONTRACT = c(r"시간\s*(?:당|단위)\s*(?:계약|정산)")
PROJECT = c(r"외주", r"의뢰", r"건\s*당", r"견적", r"프로젝트", r"납품")

# ── 근무 방식 ─────────────────────────────────────────────────────────
REMOTE_NEG = c(r"재택\s*(?:불가|안\s*됩니다|은\s*어렵)", r"원격\s*(?:불가|근무\s*불가)")
REQUIRED_WORK_VISIT = c(
    r"(?:작업|업무)(?:은|는|을|를)?\s*(?:(?:저희|우리|제)\s*)?"
    r"(?:집|사무실|현장)(?:으)?로\s*방문\s*(?:하셔야|해야)\s*(?:해요|합니다|됩니다|돼요)"
)
ONSITE = [*REQUIRED_WORK_VISIT, *c(r"주\s*\d\s*일\s*출근", r"(?<!재택\s)출근(?!\s*(?:없이|안|불필요|하지))\s*(?:근무|필수|해야|하셔야)?", r"사무실\s*(?:근무|상주)", r"사무실(?:로|에)\s*오셔서\s*근무", r"상주\s*(?:근무|개발)?", r"매장\s*근무")]
HYBRID = c(r"재택\s*(?:병행|혼합)", r"주\s*\d\s*일\s*재택", r"일부\s*재택", r"하이브리드")
REMOTE_NEGOTIABLE = c(r"재택\s*(?:여부\s*)?협의", r"원격\s*(?:근무\s*)?협의", r"근무\s*(?:형태|방식)\s*협의")
REMOTE_EXPLICIT = c(
    r"완전\s*재택",
    r"전\s*과정\s*(?:원격|비대면|온라인)",
    r"재택\s*(?:근무|작업|진행|가능|으로|환영|OK|ok)",
    r"근무(?:는|지[는:]?)?\s*자택", r"자택\s*(?:근무|작업)",
    r"원격\s*(?:근무|작업|진행|으로|가능)",
    r"비대면\s*(?:진행|작업|으로)?",
    r"온라인\s*(?:으로|로)\s*(?:만\s*)?(?:진행|작업|소통)",
    r"리모트|remote",
)
REMOTE_WEAK = c(r"(?:메일|이메일|카톡|카카오톡)(?:로|으로)?\s*(?:전달|주고받|보내|드려)", r"파일로\s*주고받", r"화상\s*(?:미팅|회의)")
FREELANCE_ONLY = c(r"프리랜서")

# ── 진행 방식 ─────────────────────────────────────────────────────────
ONSITE_REQUIRED = [*REQUIRED_WORK_VISIT, *c(
    r"(?:첫\s*(?:날|미팅|만남)|최초|초기)[^.\n]{0,25}(?:방문|대면|사무실|매장|학원|내방)",
    r"방문\s*(?:교육|미팅|회의)",
    r"대면\s*(?:미팅|교육|회의|으로)",
    r"(?:월|주)\s*\d\s*회[^.\n]{0,15}(?:회의|미팅|방문|출근)",
    r"회의\s*참석",
    r"출근(?!\s*(?:없이|안|불필요|하지))",
)]
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
NATIONWIDE = c(r"지역(?:은|이)?\s*(?:상관|무관|제한\s*없)", r"전국\s*(?:어디|누구|가능|지원)", r"거주지\s*(?:무관|상관)", r"어디서나")
REGIONAL = c(r"[가-힣]{2,6}\s*(?:거주자|거주하시는\s*분|지역\s*분)\s*만", r"(?:인근|근처)\s*(?:거주자|분)\s*만", r"출퇴근\s*가능한\s*분만?")

# ── 업무 카테고리 ─────────────────────────────────────────────────────
DEV_VERB = re.compile(r"제작|개발|구축|수정|리뉴얼|유지\s*보수|연동|자동화|만들|코딩|프로그래밍|매크로|스크립트")
# Require an actual software object, a change, and a request in the same clause.
# A technology name or a request for a portfolio's "수정 방향" is insufficient.
SOFTWARE_CHANGE_REQUEST = re.compile(
    r"(?:홈페이지|홈피|웹\s*(?:사이트|페이지|서비스|앱)|사이트|쇼핑몰|자사몰|카페24|"
    r"프로그램|소프트웨어|앱|어플|플랫폼|코드|(?i:AI)\s*에이전트|챗봇|"
    r"(?i:HTML(?:\s*/\s*CSS)?|CSS|React))"
    r"(?:\s*(?:의|을|를|코드|오류|버그|디자인|레이아웃|기능|화면|반응형|접근성|및|과|와)){0,5}\s*"
    r"(?:(?:제작|개발|구축|수정|리뉴얼|구현|유지\s*보수)\s*(?:을|를)?\s*"
    r"(?:의뢰|외주|요청|부탁|해\s*주실|해\s*주세요|해\s*주시|하실|"
    r"(?:(?:에\s*)?(?:능숙하신|익숙하신|경험\s*있으신)\s*분\s*)?(?:좀\s*)?도와\s*(?:주실|주세요))"
    r"|만들어\s*(?:주실|주세요|주시|줄))"
    r"(?!\s*(?:가|는|은|이)?\s*(?:아니|아닌|아닙|아님|없))"
)
ADVICE_REQUEST = c(
    r"수정\s*방향\s*(?:을|를)?\s*(?:제시|알려|조언)",
    r"(?:첨삭|조언|피드백)\s*(?:을|를|만|도)?\s*(?:요청|부탁|구합니다|구해요|해\s*주실|해\s*주세요)",
)
CATEGORY_RULES: list[tuple[str, list[re.Pattern[str]], bool]] = [
    # (id, patterns, 개발 동사가 없어도 개발 업무로 보는지)
    ("landing", c(r"랜딩\s*페이지", r"원\s*페이지|1\s*페이지", r"이벤트\s*페이지"), True),
    ("shop", c(r"쇼핑몰", r"카페24|자사몰", r"상세\s*페이지"), False),
    ("website", c(r"홈페이지|홈피", r"웹\s*사이트|웹사이트", r"워드프레스|wordpress", r"예약\s*페이지", r"웹\s*페이지"), False),
    ("fullstack", c(r"웹\s*(?:프로그램|서비스|앱)", r"관리자\s*페이지|관리\s*페이지", r"풀스택", r"백엔드|서버\s*개발", r"앱\s*[·/,]\s*웹|웹\s*[·/,]\s*앱", r"플랫폼\s*개발"), True),
    ("software", c(r"프로그램\s*(?:제작|개발|이\s*필요)", r"소프트웨어", r"C#|파이썬|python|자바|윈도우\s*(?:PC|프로그램)", r"변환\s*프로그램", r"(?:앱|어플(?:리케이션)?|애플리케이션)\s*(?:개발|제작|복구)", r"크롤링|크롤러|시스템\s*(?:개발|구축)"), True),
    ("vba", c(r"VBA|vba", r"매크로", r"엑셀[^.\n]{0,15}(?:자동|매크로|함수\s*개발)"), True),
    ("automation", c(r"자동화(?!물)", r"자동으로", r"API|api|(?i:RPA)", r"연동", r"알림톡", r"구글\s*(?:시트|스프레드시트)", r"스프레드시트"), True),
    ("data", c(r"데이터\s*(?:정리|입력|취합)", r"정리\s*작업", r"엑셀\s*(?:정리|취합|입력)", r"취합"), False),
]
DEV_CATEGORIES = {"landing", "shop", "website", "fullstack", "software", "vba", "automation"}
PHYSICAL_AUTOMATION = c(
    r"(?:장비|설비|기구|레일)\s*(?:기구\s*)?(?:조립|유지\s*보수|배선|설치|셋업|세팅|개조|생산)",
    r"자동화\s*(?:생산\s*라인|라인\s*생산|생산|제조)",
    r"자동화\s*라인[^.\n]{0,12}(?:생산|자재|품질)",
)
SOFTWARE_DELIVERY = re.compile(r"소프트웨어|프로그램\s*(?:개발|제작)|코딩|PLC|API|파이썬|Python|매크로|스크립트", re.I)
PHYSICAL_SHOP = re.compile(r"(?:물품|상품|제품)[^.\n]{0,20}(?:제작|포장|진열)[^.\n]{0,20}(?:포장|진열)|(?:물품|상품|제품)\s*(?:제작|포장)")
SHOP_DELIVERY = re.compile(r"상세\s*페이지|(?:쇼핑몰|자사몰|카페24)\s*(?:을|를)?\s*(?:제작|개발|구축|수정|리뉴얼)|홈페이지|웹\s*사이트")

# Marketing requests often name the existing website or landing page, while
# "제작" actually describes ad copy or an image. A software noun elsewhere in
# that request is insufficient: require a software object and its own change
# request. Actual development for a marketing business remains eligible.
MARKETING_CONTEXT = re.compile(r"광고|마케팅|홍보|캠페인|바이럴|\b(?:SEO|CPA|CPS)\b", re.I)
MARKETING_WORK = re.compile(
    r"(?:소재|이미지|문구|원고|배너|콘텐츠|영상)\s*(?:제작|디자인|작성|편집|수정)|"
    r"(?:광고|캠페인|SNS|블로그|카페|SEO)\s*(?:운영|집행|대행|관리)|(?:상위|검색)\s*노출",
    re.I,
)
MARKETING_SOFTWARE_REQUEST = re.compile(
    r"(?:홈페이지|홈피|랜딩\s*페이지|웹\s*(?:사이트|페이지|서비스|앱)|사이트|쇼핑몰|자사몰|카페24|"
    r"관리자\s*(?:페이지|화면)|프로그램|소프트웨어|앱|어플|플랫폼|코드|API|대시보드|매크로|챗봇|HTML|CSS|React)"
    r"(?:\s*(?:의|을|를|코드|오류|버그|기능|화면|폼|반응형|접근성|및|과|와|디자인|레이아웃)){0,5}\s*"
    r"(?:제작|개발|구축|수정|리뉴얼|구현|보완|개선|연동)\s*(?:을|를|이|가)?\s*"
    r"(?:의뢰|외주|요청|부탁|필요|해\s*주|하실|가능하신|견적|비용|(?:하?고|및|과|와|[+/,·]))"
    r"(?!\s*(?:가|는|은|이)?\s*(?:아니|아닌|아닙|아님|없))",
    re.I,
)

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

    staff = operational_staff_evidence(title, body)
    if staff:
        return _j("demand_intent", "employee_hiring", staff)

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
    has_verb = bool(DEV_VERB.search(text) or BUYER[0].search(text))
    out: list[str] = []
    for cid, pats, inherently_dev in CATEGORY_RULES:
        if any_match(pats, text) and (inherently_dev or has_verb or cid == "data"):
            out.append(cid)
    # Code repair is a development deliverable even without the word website.
    # Keep bare HTML/React mentions (including advice requests) inconclusive.
    change = SOFTWARE_CHANGE_REQUEST.search(text)
    if not out and change:
        out.append("website" if re.search(r"사이트|(?i:HTML|CSS|React)", change.group()) else "software")
    repair = BUYER[0].search(text)
    if not out and repair:
        out.append("website" if re.search(r"홈페이지|홈피|웹|워드프레스", repair.group()) else "software")
    # '엑셀 데이터 정리' 는 데이터 정리이지 VBA 개발이 아니다
    if "vba" in out and not re.search(r"VBA|vba|매크로|자동", text):
        out.remove("vba")
    # Physical assembly/setup is not software automation. Preserve actual
    # programming requests even when the software controls factory equipment.
    if "automation" in out and any_match(PHYSICAL_AUTOMATION, text) and not SOFTWARE_DELIVERY.search(text):
        out.remove("automation")
    # Physical goods packing is not development of the shop itself.
    if "shop" in out and PHYSICAL_SHOP.search(text) and not SHOP_DELIVERY.search(text):
        out.remove("shop")
    if (MARKETING_CONTEXT.search(text) and MARKETING_WORK.search(text)
            and not SOFTWARE_CHANGE_REQUEST.search(text)
            and not MARKETING_SOFTWARE_REQUEST.search(text)):
        out = [cid for cid in out if cid not in DEV_CATEGORIES]
    dev = any(cid in DEV_CATEGORIES for cid in out) and has_verb
    if not out:
        out = ["other"]
    return out, dev


def operational_staff_evidence(title: str, body: str) -> Ev | None:
    return find(OPERATIONAL_STAFF, body, title, "explicit", "쇼핑몰 운영 인력 모집")


def salary_evidence(title: str, body: str) -> Ev | None:
    """A salary amount is employment pay even if user feedback confirms a buyer."""
    return find([EXPLICIT_SALARY], body, title, "explicit", "급여 지급 표시")


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


def non_delivery_reason(title: str, body: str) -> tuple[str, Ev] | None:
    """Identify explicit teaching/advice/research without inventing an order.

    Delivery with handover training remains a development lead. Ambiguous mixed
    requests stay in review rather than being silently discarded.
    """
    advice = find(ADVICE_REQUEST, body, title, "explicit")
    if advice and not SOFTWARE_CHANGE_REQUEST.search(f"{title}\n{body}"):
        return "첨삭·조언 요청 — 제작 납품 의뢰 아님", advice
    primary_education = re.search(r"과외|강의|가르쳐|배우|교육해\s*주|교육\s*선생|취업.*도와|(?:제작|개발|코딩)(?:하는\s*(?:거|것|방법)|\s*방법)[^.\n]{0,12}알려", title)
    delivery_title = re.search(r"(?:제작|개발|구축|납품)\s*(?:의뢰|외주)|납품", title)
    education = find(c(r"과외", r"강의", r"교육해\s*주", r"가르쳐\s*주", r"배우고\s*싶", r"선생님|수업"), body, title, "explicit")
    if education and primary_education and not delivery_title:
        return "교육·과외 요청 — 제작 납품 의뢰 아님", education
    if education and not find(BUYER, body, title, "explicit") and re.search(r"선생님|수업|과외|가르쳐\s*주", body):
        return "교육·과외 요청 — 제작 납품 의뢰 아님", education
    if re.search(r"연구.*(?:보조|조사)|업무\s*보조", title):
        research = find(c(r"연구자료", r"자료\s*조사", r"방법\s*(?:서치|조사|검색)"), body, title, "explicit")
        if research and not find(BUYER, body, title, "explicit"):
            return "조사·보조 업무 — 제작 납품 의뢰 아님", research
    return None
