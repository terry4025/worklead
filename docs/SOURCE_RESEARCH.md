# 소스 조사 기록 — 당근알바

> 이 문서는 수집 허가가 아니다. 공개 열람 가능성과 자동 수집 허용을 같은 것으로 보지 않는다.
> 현재 상태: **자동 수집 `permission_pending`, 어댑터 잠김(조사 미완료)**. 수동 입력은 사용 가능.

## 1. 확인 시도 기록

| 시각 (KST) | 대상 | 결과 | 해석 |
| --- | --- | --- | --- |
| 2026-09-26 12:0x | `https://jobs.daangn.com/robots.txt` | 개발 컨테이너 프록시가 CONNECT 를 403 으로 거부 | **개발 환경의 네트워크 정책 차단**이며 사이트 응답이 아니다. 사이트에 대해 아무것도 확인하지 못했다. |

- `jobs.daangn.com/`, `/about`, `/s` 는 같은 이유로 요청하지 않았다 (결과를 추측하지 않음).
- 요구 문서에 적힌 2026-09-26 관찰(동네 중심 소개, 지역 기준 검색, 근무 기간·요일·시간·업무 종류 필터, 상세의 마감 표시, `/s` 가 특정 regionId 로 연결된 사례)은 **이 환경에서 재확인하지 못한 사전 기록**이다.

## 2. 확인해야 할 항목과 구현 영향

| 항목 | 상태 | 확인 전 구현 동작 | 확인 후 채울 곳 (`backend/worklead/sources/daangn/profile.json`) |
| --- | --- | --- | --- |
| 이용약관·운영정책의 자동 수집 조항 | 미확인 | 정책 `permission_pending` → 실행·예약 모두 거부 (API 409 `policy_not_allowed`) | 앱 수집 화면 → "수집 정책 검토 기록" (근거 필수) |
| robots.txt | 미확인 | 실행 시 공통 엔진이 매번 확인·준수 (5xx 는 실행 중단, Disallow 는 소스 정지) | 별도 입력 불필요 (자동) |
| 전국 단일 검색 지원 여부 | 미확인 | 지역별 계획만 가능 | `search.nationwide_url_template` |
| 공개 지역 목록과 전체성 | 미확인 | 지역 목록 없음 → 계획 불가 (`research_incomplete`) | `regions.items[]` (`param`, `label`, `sido`), 전체성이 확인되면 `regions.status = "verified"` |
| 검색 URL 형식·페이지 이동 | 미확인 | 요청하지 않음 | `search.url_template` (`{query}`, `{region}`, `{page}`), `first_page`, `max_pages` |
| 목록의 상세 링크 형식·공고 ID | 미확인 | — | `list.detail_link_pattern`, `list.post_id_pattern` |
| "결과 없음" 표시 문구 | 미확인 | 링크도 표시도 없으면 **파싱 실패로 기록** (정상 0건 아님) | `list.empty_markers` |
| 상세 구조화 데이터(JobPosting JSON-LD) 여부 | 미확인 | — | `detail.strategy = "jsonld_jobposting"` (다른 방식이 필요하면 어댑터 추가) |
| 게시일·수정일·마감 상태 필드 | 미확인 | 본문 규칙으로만 판단, 마감 표시 없음만으로 모집 중 확정 안 함 | `detail.closed_markers`, `open_markers`, `valid_through_means_open` |
| 삭제 응답의 신뢰성 | 미확인 | 404/410 도 삭제 확정 안 함 (접근 오류로 기록) | `detail.deletion_reliable` |
| 차단·CAPTCHA 페이지 표시 | 미확인 | 일반 표시(recaptcha 등)만 감지 | `detail.block_markers` |
| 로그인·앱 이동 필요 여부 | 미확인 | 401 은 소스 정지 | — |
| 재택 전용 필터 | 미확인 | 사용하지 않음 (재택 없는 글도 수집 후 판단) | `capabilities.remote_filter` |
| 요청 예산·간격 | 제안값 | 300회/일, 6초 간격 | `request_budget_per_day`, `min_interval_seconds` (정책이 더 엄격하면 따름) |

`verified` 는 위 항목을 실제로 확인하고 이 문서에 근거(URL·시각·응답·스크린샷 위치)를 남긴 뒤에만 `true` 로 바꾼다.

## 3. 조사 절차 (허용된 범위에서 손으로 수행)

1. 이용약관·운영정책·robots.txt 를 읽고 자동 수집 관련 조항을 인용과 함께 기록한다.
2. 브라우저로 공개 페이지를 소수만 열어 아래를 기록한다: 확인 URL, 시각, 응답 코드, 리다이렉트, 로그인 요구 여부.
3. 검색 1회·상세 2~3건의 HTML 을 저장해 `backend/tests/fixtures/daangn/` 에 넣는다 (개인정보 제거, 허용 범위 내).
4. 저장 표본으로 `profile.json` 값을 채우고, `tests/fixture_site.py` 와 같은 방식의 표본 테스트를 추가한다.
5. 약관상 허용 근거가 있으면 앱에서 정책을 `allowed`/`restricted` 로 기록한다 (근거 텍스트 필수). 불허이면 `blocked` 로 기록하고 수동 입력만 쓴다.

금지: CAPTCHA 우회, 계정·세션 사용, 로그인·지역 인증 우회, 프록시·계정 회전, 비공개 API·지역 ID·커서 추측, 대량 식별자 탐색.

## 4. 다른 곳에서 조사하려면

이 개발 환경에서 조사를 이어가려면 환경 설정의 Network access 에 `jobs.daangn.com` (약관 확인용으로 `www.daangn.com`) 을 허용 도메인으로 추가해야 한다. 그 전까지는 사용자의 PC 에서 위 절차를 수행한다.
