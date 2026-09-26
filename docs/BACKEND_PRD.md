# 제품 요구 정리 (PRD)

원본 요구: [REQUIREMENTS.md](REQUIREMENTS.md). 이 문서는 구현 기준과 판정 규칙을 정리한다. 실제 구현·검증 상태는 [STATUS.md](STATUS.md).

## 목적

아직 사람을 구하고 있고, 원격으로 수행해 실제 대가를 받을 수 있는 **개발·자동화 제작 의뢰**를 빨리 찾아 판단하게 한다. 공고를 많이 모으는 것이 목표가 아니며 수주·수익을 보장하지 않는다.

## 사용자

웹사이트·풀스택·랜딩페이지·프로그램·업무 자동화·매크로/VBA 를 만들어 수익을 얻으려는 개인 개발자. Windows 10/11 데스크톱.

## 범위

| 포함 | 제외 |
| --- | --- |
| 당근알바의 **허용된** 공개 구인글 (조사·정책 확인 후) | 당근 중고거래·동네생활·비즈프로필·채팅 검색 |
| 전국 게시 지역을 목표로 한 순환 탐색 (실제 확인 범위는 따로 표시) | 전국 전수 확보 보장 |
| 수동 텍스트/CSV/JSON 입력 | URL 만 넣으면 대신 내려받는 기능, 만능 크롤러 |
| 원문 열기, 메모, 진행 상태, 재확인, 문의 초안, 계약·수금 기록 | 자동 지원·채팅·이메일 발송, 계약 확정, 결제, 회계·세무 |
| 규칙 분석 (AI 없이 동작), 선택적 AI 인터페이스 | 무거운 로컬 모델 기본 포함 |

## 판정 규칙 (요약)

- 축은 서로 독립: 요청 유형 / 계약 형태 / 재택 / 진행 방식(대면) / 지원 지역. 근거가 없으면 unknown.
- 상태 축은 분리: 모집(source_status) / 접근(access_status) / 분석(analysis_status) / 영업(sales_stage). 접근 오류 ≠ 마감, 페이지 존재 ≠ 모집 중, 원문 마감 ≠ 영업 실패.
- 추천 = 구매 수요 + 개발·자동화 업무 + 최근 모집 확인(TTL) + 완전 재택·온라인 근거 + 지원 지역 충족 + 위험 신호 없음. 불명확하지만 유망하면 확인 필요. 마감·판매자 홍보·출근 필수·정규직·위험 신호·관련 없음은 제외.
- 예산 없음은 "예산 확인 필요"이지 0원·부적합이 아니다.
- 우선순위 점수는 설명 가능한 규칙 점수이며 수주 확률이 아니다. 모르는 요소는 미평가로 분리한다.
- 수익성: 사실(원문)·사용자 입력(프로필)·추정(투입 시간)을 구분. 시급·월급은 환산하지 않음. 예상 기여액은 세후 순이익이 아니다.
- 영업 기록: 계약 확인·합의 금액·실제 수금·환불·직접 비용·증빙을 분리. won 표시만으로 수금이 늘지 않는다.

## 수집 정책

- 사이트 소스 기본 정책은 `permission_pending`. 사용자가 근거와 함께 허용을 기록하고, 조사 프로필이 검증되어야 실행된다.
- 공통 엔진이 robots.txt·허용 호스트·요청 예산·간격을 강제한다. 차단(401/403/CAPTCHA)은 소스 정지, 429 는 Retry-After 유예.
- 파싱 실패·차단 페이지를 "신규 0건, 정상 완료"로 기록하지 않는다.

## 완료 기준 (요구 §15 검증 사례)

| # | 사례 | 검증 위치 |
| --- | --- | --- |
| 1 | 반복 수집에도 리드·알림 중복 없음 | `backend/tests/test_engine.py::test_nationwide_rotation_dedup_and_paths` |
| 2 | 다른 지역에서 같은 공고 → 원본·발견 경로 보존 | 같은 테스트 (discovery_paths, found_in) |
| 3 | 전국 중 일부만 확인 → 부분 수집 표시 | `test_partial_coverage_is_reported`, `test_429_halts_and_next_run_resumes` |
| 4 | 지역 ID 미확보 → 추측 없이 범위 미확인 | `test_daangn_not_runnable_until_research` |
| 5 | 재택 명시·첫날 방문·쇼핑몰 출근·재택 정규직·미언급 구별 | `test_analysis.py` (remote 관련 테스트) |
| 6 | 판매자 광고 vs 제작 의뢰 | `test_seller_vs_buyer` |
| 7 | 시급·월급·건당·협의·예산 없음 보존 | `test_pay_preserved`, `test_no_budget_is_not_zero_and_not_excluded` |
| 8 | 마감일·근무일·게시일·최초 수집일 분리 | `test_deadline_vs_work_start` + 모델 필드 분리 |
| 9 | 403/429/CAPTCHA/파서 오류 ≠ 정상 0건·마감 | `test_403_…`, `test_captcha_…`, `test_429_…`, `test_parse_failure_…` |
| 10 | AI 장애에도 원문·규칙 결과 조회 | `test_api.py::test_ai_failure_keeps_rules_and_does_not_retry_same_input` |
| 11 | 강제 종료 후 이어받기 중복 없음 | `test_crash_resume_no_duplicates` |
| 12 | 적격성 미충족은 점수만으로 추천 불가 | `test_high_score_but_ineligible_is_not_recommended` |
| 13 | 사용자 수정·메모 유지 | `test_user_state_survives_reanalysis_and_recollection` |
| 14 | won 만으로 수금 증가 없음 | `test_won_does_not_increase_collected` |
| 15 | 새 어댑터 연결 시 공통 점수·UI 수정 없음 | 합성 사이트 어댑터가 프로필만으로 연결됨 (`tests/fixture_site.py`) |
| 16 | 깨끗한 Windows 에서 설치·실행·백업·복원·종료 | **미검증** (STATUS.md) |

## 성능 목표

로컬 10만 리드에서 일반 필터 목록 p95 500ms (초기 목표). **아직 측정하지 않음** — 측정 시 하드웨어·질의·표본을 함께 기록한다.
