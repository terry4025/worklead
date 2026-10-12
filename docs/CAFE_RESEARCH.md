# 당근 카페 M0 조사 (2026-10-12)

상태: **M0 진행 중 — 공개 화면·사용자 로그인·웹 편집기 확인, 세션 재시작 검증 등 미완료**. M0 전체 완료나 M1~M5 구현 완료를 뜻하지 않는다. 실사이트 게시·댓글 작성·카페 가입은 실행하지 않았다.

## 작업 시작과 보존

- `docs/CAFE_AUTOMATION_PRD.md` 전체와 §0의 선행 문서·코드를 읽었다.
- `feat/daangn-cafe`를 만들고 기존 변경을 `5aa3395` (`chore: snapshot working tree before cafe feature`)에 보존했다. push하지 않았다.
- `artifacts/daangn-cafe-20261012/installed-before/`에 설치 GUI·backend를 복사하고 SQLite 온라인 백업을 만들었다. 백업 시각 2026-10-12 18:48:19 KST, 리드 697건, `0005_page_checkpoints`, `integrity_check=ok`. 운영 DB로 실험하지 않았다.
- 조사 브라우저는 시스템 Edge 156.0.4314.15와 전용 `%LOCALAPPDATA%\Worklead\cafe-browser\`다. Chrome 기본 프로필은 사용하지 않았다. 쿠키·프로필은 커밋·백업하지 않는다.

## 확인 URL·시각·화면 근거

페이지는 화면에서 관찰된 링크를 따라 이동했다. 카페 홈 최초 주소는 공식 카페 검색 색인에서 발견한 뒤 실제 Edge 응답으로 재검증했다. 직접 비공개 API·GraphQL 호출은 하지 않았다. 이동 간격은 최소 8초다. 최초·재방문 시각과 HTTP 상태 원본은 `artifacts/daangn-cafe-20261012/navigation.json`에 있다. 아래 시각은 KST다.

| 대상 | 직접 확인 주소 | 시각 | 결과·화면 근거 |
| --- | --- | --- | --- |
| 메인 robots | https://www.daangn.com/robots.txt | 18:48:49 | 200. `robots-20261012.txt` |
| 카페 robots | https://cafe.daangn.com/robots.txt | 18:48:59 | 200. `cafe-robots-20261012.txt` |
| 카페 목록 | https://cafe.daangn.com/ | 18:50:26, 18:53:43 | 200. 카테고리 navigation·카페 목록·더보기 관찰 |
| 직업 카테고리 | https://cafe.daangn.com/categories/career | 18:50:39 | 200. 화면의 `직업/커리어` 링크를 따라 확인 |
| 카페 홈 | https://cafe.daangn.com/dangialbaneun-y | 18:49:19, 18:55:27 | 200. `home.html` |
| 구인구직 게시판 | https://cafe.daangn.com/dangialbaneun-y/boards/아르바이트구인구직-LdX22G8A | 18:50:58 | 200. `board.html` |
| 기존 동일 제목 글 | https://cafe.daangn.com/dangialbaneun-y/posts/💻-웹사이트프로그램-개발해-드립니다-컴공-출신-프리랜서-2년차-PoVNgxZA | 18:49:40, 18:55:45 | 200. `post.html` |
| 댓글 있는 글 | https://cafe.daangn.com/dangialbaneun-y/posts/안녕하세요-PnNBE2DP | 후속 확인 | 200. `comments.html`; 저장 시각은 표본 `provenance.json` |
| 웹 글쓰기 | https://cafe.daangn.com/dangialbaneun-y/posts/new | 로그인 후 후속 확인 | 200. `editor.html`; 게시 실행 없음 |

표본 경로는 `backend/tests/fixtures/daangn_cafe/`다. 실제 DOM의 태그·class·role을 유지하고, 카페·글·닉네임·본문·연락처·숫자·링크는 합성 값으로 바꿨다. 스크립트·이미지 주소·이벤트 속성·폼 값은 제거했다. 이 HTML은 실제 사이트 호출 없이 테스트를 만드는 구조 표본이며, 동적 사이트의 기능을 구현한 가짜 사이트는 아직 아니다.

## 공개 화면에서 확인한 사실

1. 실제 카페 호스트는 `cafe.daangn.com`이다. `www.daangn.com`의 robots만 검사해서는 카페를 판단할 수 없다.
2. 비로그인 Edge에서 목록·카테고리·카페 홈·게시판·글 제목과 본문·기존 댓글을 읽을 수 있었다. 이 공개 열람은 자동 수집 허가와 별개다.
3. 목록에는 카테고리 navigation과 `더보기`가 있다. `직업/커리어`, `IT/모바일`, `동네/지역` 카테고리 링크를 관찰했다. 검색 input과 독립적인 지역 필터는 관찰한 공개 데스크톱 화면에서 찾지 못했다. 검색 URL·지역 ID·페이지 커서를 추측하지 않는다. 로그인 후 차이를 확인해야 한다.
4. 카페 홈에 이름·개설일·설명·지역·카테고리·멤버 수·게시글 수·게시판 링크·공지 링크가 표시됐다. 표본 카페는 당시 멤버 2,980명·게시글 2,118건이며, 이 수치는 현재·전체 시장을 보장하지 않는다.
5. 게시판 navigation의 `아르바이트구인구직`은 게시판 후보 근거다. 이름만으로 홍보가 허용됐다고 확정하지 않는다. 공지와 게시판 설명의 규칙 검사는 남아 있다.
6. 게시글 제목은 `h1`, 본문 문단은 `div.article-body`이며 `whitespace-pre-wrap` 표시가 있었다. 기존 기본 템플릿과 같은 글에서 50개 문단 중 24개가 `U+3164`만 담고 있었다. 이 관찰은 이번 자동 입력·게시가 성공했다는 증거가 아니다.
7. 댓글 작성 UI는 `role=group`의 `aria-label="댓글 작성"`, `textarea`의 `aria-label="댓글 입력"`다. 기존 댓글에 `이전 답글 1개 더보기`, `댓글 전체보기` 버튼이 있었다. 댓글 작성·답글 버튼은 누르지 않았다. 안정적인 댓글 ID·전체 페이지 이동·작성 시각 정밀도는 추가 조사해야 한다.
8. 기본 템플릿과 같은 제목·본문·포트폴리오 링크의 글이 표본 카페에 이미 있었다. 현재 사용자 계정의 글인지 인증 상태로 확인하지 못했다. 신규 게시 대상 선정 시 반드시 내 글과 대조해야 하며, 본인 글이면 `already_exists`로 건너뛴다.
9. `채팅하기` 링크는 `https://chat.daangn.com/login`이었다. 로그인 후 실제 웹 채팅을 읽을 수 있는지는 미확인이다. 이번 범위에서는 채팅 구현을 하지 않는다.
10. 사용자 직접 로그인 후 목록 header의 `로그인`이 사라지고 `aria-label="사용자 메뉴"`가 생겼다. 새 카페 페이지에서도 동일하게 확인했다. 로그인된 표본 카페에는 가입 버튼 대신 `글쓰기` 링크가 있었다.
11. 관찰한 글쓰기 링크는 `/dangialbaneun-y/posts/new`였다. 제목은 `input[type=text]`와 `placeholder="제목을 입력해주세요."`, 본문은 `div.ProseMirror[contenteditable=true]`이고, 빈 본문에 paragraph가 있다. ProseMirror 편집기이며 게시판 선택 버튼과 `글쓰기` 버튼 2개를 확인했다. 제목·본문 입력, 게시, 공개 범위·검색 허용 설정 변경은 실행하지 않았다.
12. 계정 메뉴에는 `로그아웃`만 보였다. `내가 쓴 글` 링크나 닉네임 필터는 아직 확인하지 못했다. 로그인된 기존 글의 댓글 작성 avatar도 `내 프로필 사진`만 표시하여 닉네임을 추측하지 않았다. 게시 중복 대조·내 글 제외 방식은 추가 조사해야 한다.

## robots와 정책

- `www.daangn.com`: `User-agent: *`에 `/kr/community/s/`, `/kr/group/s/` 금지가 현재도 있었다. 카페 전용 경로 금지 여부만으로 별도 호스트의 허용을 추론하지 않는다. 검색/AI bot 전용 그룹에는 별도 금지 규칙도 있었다.
- `cafe.daangn.com`: `User-Agent: *`의 `Allow: /`와 `/api/`, `/debug/`, `/errors/`, `/-/manage/` 금지를 확인했다. 여러 AI bot 그룹은 `Disallow: /`다. 원문을 그대로 보존했다. User-Agent를 위조하거나 이 규칙을 우회하지 않는다.
- 기존 `SOURCE_RESEARCH.md`에 기록된 약관상 사전 서면 동의 요구와 홍보 제재 위험을 PRD §2의 확인 문구에 반영해야 한다. M0에서 기능 확인 동의나 설치의 정책 `allowed` 기록을 대신 만들지 않는다.

## 로그인 조사 상태와 실제 오류

사용자가 직접 로그인하도록 보이는 Edge 창을 열었다. 전화번호·인증번호·비밀번호는 입력·저장하지 않았다.

첫 조사에서 존재하지 않는 정확한 heading을 기다린 도구 호출이 시간초과로 JavaScript 세션과 Edge를 종료했다. 이 오류는 사이트가 웹 글쓰기를 지원하지 않는다는 뜻이 아니다. 이후 호출 timeout을 줄이고, 관찰된 요소만 사용한다. 재시작된 전용 프로필에는 카페의 `로그인` 버튼이 남았다. 두 복원된 인증 탭에는 `QR코드 로그인 실패 / 인증코드가 만료됐어요`가 실제 표시됐다. 사용자 요청으로 만료 탭을 정리하고 새 인증 탭 하나를 열었다.

이후 새 인증 탭에서 사용자 로그인 성공과 새 페이지의 인증 상태를 확인했다. 웹 글쓰기 URL·ProseMirror 편집기도 확인했다. 마지막 Windows Computer Use 화면 확인 호출은 **현재 브라우저 URL을 충분한 신뢰도로 식별할 수 없어 정책을 적용할 수 없다**는 도구 보호 중단을 반환했다. 이는 사용자의 로그인 실패나 Windows 자동화 자체 미지원과 다르다. 해당 중단 뒤 화면 입력을 더 하지 않았다.

**남은 M0:** 영속 프로필 정상 종료/재시작 뒤 세션 유지, 내가 쓴 글/닉네임 필터 또는 소유자 식별 경로, 줄 단위 Enter 입력 방식과 저장 후 빈 줄 보존, 공지 규칙, 댓글 ID/답글/전체 페이지 이동, 웹 채팅 읽기 여부. 실제 저장 검증은 사용자가 고른 카페 1곳에서 M3의 가짜 카페 검증을 통과한 뒤에만 한다. 인증 실패·도구 보호 중단을 우회하지 않는다. 아직 설계 성립 전체를 확정하거나 M1 구현을 시작하지 않았다.

## 구현 연결점과 브라우저 선택 검토

- 브라우저 수집 결과는 `engine/pipeline.py:ingest()`에 `ParsedPost`로 전달한다. 기존 정규화·중복·규칙·유사 관계·사용자 기록 보존을 재사용하고, 커밋 뒤 기존 AI coordinator wakeup을 따른다. HTTP Fetcher 대신 F0의 단일 큐·호스트/robots 검사·일일 한도·고정 이동 간격을 적용해야 한다.
- 기존 워커는 collector/interactive 두 스레드다. Playwright 객체는 카페 전용 소유 스레드에서만 사용해야 한다. 일반 expired run 자동 재시도는 불확실한 게시 제출에 그대로 적용하지 않는다. 제출 전 pending을 저장하고 내 글 대조로 복구한다.
- SQLite 백업은 새 테이블을 포함하지만, `storage.validate_database()`의 필수 테이블 검사를 버전에 맞춰 확장해야 0001~0005 백업 복원이 계속 가능하다. 복원 때 자동 게시를 끄고 오늘 이동/게시 한도와 최근 게시 간격을 보존해야 한다.
- 알림은 기존 outbox/native 소비자를 재사용하되 카페 문의 enum·설정·Rust 고정 문구를 함께 연결한다. 진단 zip에는 상태·집계만 넣는다.
- 현재 Python 의존성에는 Playwright가 없다. 조사에는 저장소의 JS Playwright 1.56.1로 시스템 Edge를 사용했다. node_repl에서 `channel="msedge"`만 지정한 초기 호출은 도구 환경의 ProgramFiles 경로 확인 실패였고, 실제 확인한 Edge 경로를 함께 지정하여 실행했다. 제품의 Python 실행 실패로 표현하지 않는다.
- [Playwright 공식 브라우저 문서](https://playwright.dev/python/docs/browsers)는 설치된 Edge의 `msedge` 채널을 지원한다. [공식 라이브러리 문서](https://playwright.dev/python/docs/library#pyinstaller)는 PyInstaller 사용을 설명하며 Windows subprocess에 Proactor 루프가 필요하다고 명시한다. Worklead API의 Selector 루프와 전용 브라우저 스레드를 분리할 수 있다.
- **잠정 선택:** PRD의 Python Playwright + 시스템 Edge 전용 프로필을 유지한다. 기존 WebView2는 내장되어 있으나 별도 창·DOM 제어·Python 수집기 브리지가 추가로 필요하다. Python 드라이버 번들 크기·frozen exe의 실제 실행·프로필 재사용은 M1/M5에서 증명해야 하며, 아직 대안 전환 근거는 없다.

## 검증 구분

M0 조사와 기능 자동 테스트는 별개다. 기존 코드 기준 TypeScript 검사와 UI 238개는 통과했다. 백엔드는 928개 중 926개 통과·1개 실패·1개 skip이다. 실패는 `test_ai_api_quality.py::test_disabled_analysis_is_no_longer_permanently_pending`에서 `pending` 대신 `rules_only`가 나온 기존 기준 상태다. 카페 제품 코드 수정 전에 발생했으며 숨기거나 통과로 기록하지 않는다. skip은 별도 opt-in Windows 자격 증명 OS 검사다. 원본 XML과 결과는 `artifacts/daangn-cafe-20261012/`에 있다. 카페 기능 자동 테스트·패키지·실제 게시·알림 검증은 미수행이다.
