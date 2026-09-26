# 연동 계약 (화면 ↔ 로컬 API)

- 계약 버전: `2026-09-26.1` (`backend/worklead/config.py: CONTRACT_VERSION`, `/v1/bootstrap.contract_version`)
- 원본: `backend/worklead/api/schemas.py` (Pydantic) → 생성물: `contracts/openapi.json`, `contracts/schemas/*.schema.json`, `contracts/client/schema.d.ts`
- 예제: `contracts/fixtures/` — 실제 API·엔진으로 생성 (데모 시드 + 합성 사이트 시나리오 normal/partial/blocked/parse_failure). `backend/tests/test_contracts.py` 가 스키마로, `desktop/src/__tests__/contract-mapper.test.ts` 가 화면 매퍼로 다시 검증한다.
- 재생성: `scripts/gen-contracts.sh`

## 1. 연결

| 환경 | 주소·토큰을 얻는 방법 |
| --- | --- |
| 설치 앱 (Tauri) | `invoke('backend_connection')` → `{ base_url, token }` (백엔드 준비까지 최대 45초 대기, 실패 시 오류 문자열). 재시작: `invoke('restart_backend')` |
| 브라우저 개발 | 백엔드 `--dev` + `WORKLEAD_DEV_TOKEN`, 화면 `VITE_DATA_MODE=live VITE_WORKLEAD_API_URL VITE_WORKLEAD_TOKEN` (`scripts/dev.sh`) |

sidecar 준비 줄 (stdout 1줄, JSON): `{"event":"ready","port":<int>,"token":"<str>","pid":..,"version":"0.1.0","api":"v1","mode":"live|demo"}` · 실패: `{"event":"error","code":"already_running|startup_failed|port_unavailable|startup_timeout","message":".."}`

## 2. 공통 규칙

- 모든 `/v1` 요청: `Authorization: Bearer <token>`. 토큰을 URL 에 넣지 않는다.
- Host 는 `127.0.0.1`/`localhost` 만. Origin 은 `http://tauri.localhost`, `https://tauri.localhost`, `tauri://localhost` (개발 모드: `http://127.0.0.1:5173`, `http://localhost:5173`). 와일드카드 없음.
- 오류: `{"error":{"code","message","details","request_id","retryable"}}` + `X-Request-Id` 헤더.
- 시각: UTC ISO 8601. 화면은 한국 시간으로 표시.
- 금액: 최소 통화 단위 정수(KRW=원) 또는 `null`. `null` 은 0원이 아니다. 단위 `project|hour|day|week|month|negotiable|unknown` (연봉 등 목록 밖 단위는 `unknown` + 원문 보존).
- 날짜 정밀도: `{at, precision: exact|day|approximate|unknown, raw}`. 게시일·최초 발견·마지막 확인·구인 마감·작업 시작은 서로 다른 필드.
- 장기 작업: `202 {job_id, run_id}` → `GET /v1/runs/{id}` 또는 이벤트로 추적.

## 3. 엔드포인트

| 메서드·경로 | 설명 |
| --- | --- |
| `GET /v1/health` | 상태 (db, workers) |
| `GET /v1/bootstrap` | 모드(live/demo), 버전, 업무 카테고리, **대기열 정의**, 재확인 TTL, 현재 이벤트 seq |
| `GET /v1/sources` | 소스 목록: 정책·robots·조사 상태(`research.ready/missing`)·건강·기능 선언·**Coverage** |
| `PATCH /v1/sources/{id}` | `auto_collect_enabled`, `interval_minutes`, `clear_stop`, `policy{status,basis,note}` (허용·제한은 근거 필수) |
| `POST /v1/runs` | `{source_id, kind: discovery|recheck_recent|recheck_stale, idempotency_key?}` → 202. 정책 미허용 409 `policy_not_allowed`, 조사 미완료 409 `research_incomplete`, 정지 409 `source_stopped`, 중복 실행 409 `run_already_active` |
| `GET /v1/runs`, `GET /v1/runs/{id}` | 실행 목록·상세 (진행률, 카운트, 오류, retry_after, 결과) |
| `POST /v1/runs/{id}/actions` | `{action: pause|resume|cancel}` |
| `GET /v1/leads` | 목록. `queue`, 필터, `sort`, `limit(≤100)`, `cursor` → `{items, next_cursor, has_more}` |
| `GET /v1/leads/queue-counts` | 같은 필터 기준 대기열별 건수 |
| `GET /v1/leads/{id}` | 상세: 근거(`evidence[field][]` + 본문 위치), 점수 구성, 분석, 위험, 수익성, 초안, 메모, 결과 기록, 관련 글, 발견 경로, 활동, 내 확인 |
| `PATCH /v1/leads/{id}` | `user_mark`(interested/dismissed/null), `sales_stage`, `memo`, `draft_text` |
| `POST /v1/leads/{id}/recheck` · `/reanalyze` | 202. 수동 입력 리드 재확인은 409 `unsupported`, 정책 미허용 소스는 409 |
| `POST /v1/leads/{id}/feedback` | `{kind: remote|real_request, value: confirmed|denied|yes|no|clear}` — 자동 판정과 별도 저장, 근거 유형 `user_confirmed` |
| `POST /v1/leads/{id}/outcomes` | 계약 확인·수금·환불·직접 비용 (`amount ≥ 0` 또는 null). won 표시만으로 수금이 늘지 않음 |
| `POST /v1/leads/{id}/draft` | 문의 초안 생성 (발송 없음). 직접 수정한 초안이 있으면 409 |
| `GET/PATCH /v1/settings` | 프로필, 재확인 TTL, 알림(조용한 시간), AI(동의 없이 켜기 불가, 키 값은 반환하지 않음), 원문 보존, 검색어 묶음(버전 증가) |
| `POST /v1/imports` | `{format: text|csv|json, content(≤2MB), file_name?, original_url?}` → 202. URL 을 대신 내려받지 않음 |
| `POST /v1/exports` | `{kind: leads_csv|leads_json|diagnostics}` → 202, 결과 파일명은 run.result. CSV 수식 주입 방지 |
| `GET /v1/metrics` | 수집 운영 / 리드 품질 / 영업 성과 분리 (기간 30일, 분모 명시) |
| `GET /v1/events` | SSE (아래) |

### 대기열 (`queue`)

| id | 조건 |
| --- | --- |
| `recommended` · `needs_review` · `auto_excluded` | 자동 판정별, 내가 제외하지 않았고 진행 전(new·reviewing) |
| `interested` | 관심 표시 + 진행 전 |
| `active` | contacted · negotiating · won · on_hold |
| `dismissed` | 내가 제외 (또는 sales_stage=ignored) |
| `all` | 전체 |

### 필터

`keyword`, `source_id`, `category`, `remote(any|confirmed|confirmed_or_inferred|unknown|onsite)`, `recruit(any|not_closed|open_fresh|recheck|unknown|closed|access_issue)`, `intent(any|buyer|hiring|seller|other)`, 다중값 `work_mode`·`recommendation`·`source_status`·`sales_stage`·`pay_unit`, `checked_since`, `sort(priority|published|checked)`.
커서는 정렬값+ID(동률 처리)를 담고 필터 해시를 검사한다. 필터가 바뀌면 400 `invalid_cursor`.

## 4. 이벤트 (SSE)

- `GET /v1/events?after_seq=<n>` 또는 `Last-Event-ID`. EventSource 는 헤더를 못 보내므로 **fetch 스트리밍**으로 읽는다 (`contracts/client/index.ts: streamEvents`).
- 형식: `id: <seq>` / `event: <type>` / `data: {"id","seq","type","at",...참조 ID}`. 15초마다 keepalive 주석.
- 종류: `run.progress`, `run.state_changed`, `lead.created`, `lead.updated`, `source.health_changed`, `analysis.completed`, `notification.created`(kind: new_lead|lead_changed|source_issue, `suppressed_reason`: 조용한 시간), `stream.reset`.
- 재연결: 마지막 seq 부터 이어받고 받은 seq 는 버린다. 보존 범위(최근 5000건)를 벗어나면 `stream.reset` → 전체 재조회.
- 같은 공고 재수집은 `lead.created`·새 리드 알림을 다시 만들지 않는다 (dedupe_key).

## 5. 화면 쪽 규칙

- 원문은 텍스트로만 표시 (HTML 실행 없음). 근거 강조는 `span` 위치로 한다.
- 외부 링크는 http/https 만 (`platform/external.ts`), Tauri 에서는 opener 플러그인(허용 URL 도 http/https 로 제한).
- CSP: `default-src 'self'; connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:*` 등 (`tauri.conf.json`).
- 데모 모드는 항상 "데모 데이터" 배지를 표시하고 운영 데이터와 섞지 않는다.

## 6. 변경 이력

| 버전 | 내용 |
| --- | --- |
| 2026-09-26.1 | 최초 계약 |
