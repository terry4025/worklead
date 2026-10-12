# 아이보스 합성 회귀 표본

2026-09-30 비로그인 공개 HTML에서 다음 DOM을 직접 확인했다.

- 전체 분류 목록: `https://www.i-boss.co.kr/ab-1957?sort=now`
- 실제 마지막 6페이지: `https://www.i-boss.co.kr/ab-1957?sort=now&PB_1415256624=6`
- 공개 상세: `https://www.i-boss.co.kr/ab-1958-88584`
- 목록: `proposal_bd`, `cell`, `txt` 아래 상세 링크, `ABA-list-paging`의 실제 다음 링크.
- 상세: `BD1957-view`, `Article-subject`, `request-info`의 `budget`/`close_date`, `ABA-view-body`.
- 연락처: 본문 밖 `contact-container`의 별도 로그인 안내.

이 디렉터리 HTML은 위 구조를 재현한 **합성 fixture**다. 공고 ID, 본문과 연락처는 테스트 값이며 실제 구매 수요·최신성의 증거로 쓰지 않는다. 원본 전체 HTML이나 로그인/연락처 데이터는 저장하지 않았다. 빈 목록 문구·구조 변경·날짜/마감 경계는 테스트에서 변형한다.

`detail-observed.html`, `closed-observed.html`은 23:27 KST경 실제 HTTP 응답에서 제목/의뢰조건/본문/마감 컨트롤 DOM을 추출하고 제목·본문을 합성 문구로 교체한 표본이다. 날짜와 마감 span 사이 공백 없는 구조, 마감된 의뢰의 `dline` 컨트롤을 보존했다. 원본 공개 페이지에서 파싱한 결과 집계·HTTP 상태·해시는 소스 디렉터리 `research/final-detail-proof.json`에 있다. 개인 연락처·작성자 정보·원본 본문은 저장하지 않았다.

`list-installed-ua.html`, `detail-installed-ua.html`은 설치 앱의 `WorkleadLocal/0.1.0 (+personal desktop lead review)` 사용자 에이전트로 확인한 간결한 HTML 구조다. 목록은 `cell > 상태영역 > a > subject`이며 `txt`가 없다. 페이지 이동은 `proposal_bd` 밖의 `BD1957_LF`에 있고, 상세 제목 영역은 `view-head`다. 목록 20개 항목의 ID·제목과 상세 제목·본문을 합성 값으로 바꾸고 연락처 영역을 제외했다. 두 구조 모두 정상 지원하되 실제 상세 링크 누락·중복 등 알 수 없는 의뢰 구조는 오류로 남긴다.
