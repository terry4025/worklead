# 연동 계약 (화면 ↔ 로컬 API)

## 자동 지원·조회 복구 계약 (2026-10-25)

후속 복구 계약: 확장 전용 `/review`의 ready 응답에 `receipt_token`을 추가한다. 이는 제출 lease와 별개이며 현재 연결의 시도·계정·소개에 묶인 15분 메모리 권한이다. POST `/reconcile`은 `{attempt_id,receipt_token,account_hash,profile_hash,evidence:"cancel_button"}`만 받아 기존 허가/journal을 대조하고 완료를 확정한다. 중복 완료 응답은 부작용 없이 성공한다. POST `/pause` `{reason:"login_required"|"account_changed"}`는 기존 receipt/lease를 폐기하고 새 연결까지 정지한다. 기존 exact Origin/Host/Bearer/2,048바이트 경계를 공통 적용한다. 새 제출 권한·공개 main API는 추가하지 않는다. 확장 복구는 최대 3회 읽기 전용이며 자세한 흐름은 `JOB_AUTO_APPLY_RECOVERY.md`를 따른다.

GET `/v1/jobs/auto-apply/status`, POST `/start` `{confirm:true,min_score:50,daily_limit:20}`, POST `/stop`을 추가한다. start 응답은 `{connection:{url,token},status,extension_directory}`이며 no-store이고 연결 비밀은 UI ref에만 보관한다. 자동 지원은 미읽음 감시와 다른 포트·임시 키를 사용한다. 확장 전용 POST `/next`는 공고 작업, `/review`는 화면 검토 결과, `/permit`은 짧은 1회 제출 허가, `/result`는 공식 지원 취소 표시를 근거로 한 완료/미확인을 받는다. 크기 2,048바이트·확장 Origin·Host·독립 Bearer·필드·상태 순서를 검사한다. 기본 50점은 경계 포함이며 직전 재분석·모집·프로필·허가·일일 한도를 모두 대조한다. 별도 중복 기록은 날짜/공개 공고 키 해시만 기록하고 DB 복원으로 지워지지 않는다. 실제 계정/소개/연결 키는 영속 로그에 쓰지 않는다. 기존 초안 API는 계속 제출을 거부한다.

유한 GET 조회는 연결 획득부터 응답 본문 파싱까지 15초 상한을 적용해 `request_timeout`(재시도 가능)을 반환한다. SSE는 별도 장기 연결이며 이 상한을 적용하지 않는다. 변경/제출 요청을 자동 재전송하지 않는다. 목록 자동 재조회는 진행 중 조회를 취소하지 않고, 한 대기열의 오류를 다른 대기열의 로딩 화면으로 숨기지 않는다. 최초 조회 실패도 30초 주기로 복구하며, 서버/수집 속도를 이 조회 상한과 혼동하지 않는다.

로컬 QueryClient는 조회·명령 모두 `networkMode: 'always'`를 사용한다. 외부 인터넷의 오프라인 판정으로 루프백 조회를 일시 중지하지 않으며, 명령을 연결 복구 시점으로 미뤄 재생하지 않는다. 명령 자동 재시도는 꺼져 있다. 이전 기본값의 조회 일시 중지와 수정 후 오프라인 조회·재조회·명령 1회 실행은 `local-query-offline.test.tsx`로 재현한다. 동작 기준은 [TanStack Query 네트워크 모드](https://tanstack.com/query/latest/docs/framework/react/guides/network-mode)를 따른다.

## 기존 크롬 연결 추가 계약 (2026-10-16)

POST `/v1/jobs/messages/chrome/start` `{confirm:true}`는 `{connection:{url,token},status,extension_directory}`를 반환한다. 키는 메인 API와 다른 임시 권한이며 응답은 no-store다. 화면은 비밀을 조회/변경 캐시에 저장하거나 표시하지 않고 복사만 제공한다. 매 시작마다 이전 키·순서를 폐기하며 GET 전체 `/v1/jobs/messages/status`는 선택한 provider=edge/chrome의 상태만 반환한다. 일반 stop은 양쪽 감시를 중지한다. Edge/Chrome 동시 시작은 409다.

Chrome 확장은 사용자가 직접 설치하고 선택한 당근 탭에서 연결한다. 별도 127.0.0.1 HTTP의 `/observe`만 exact 확장 Origin/Host/Bearer/JSON 512바이트와 `{state,unread_count,sequence}`를 검증해 받는다. 일반 앱 API의 Origin/인증은 확장에 개방하지 않는다. 첫 관찰과 로그인·DB 복원은 기준값이며, heartbeat 공백 뒤 늘어난 표시는 별도 재개 확인 알림으로 처리한다. 실제 신규 메시지 도착 시각을 추정하지 않는다. `scripts/gen-contracts.sh --chrome-only`로 이 계약만 갱신한다. 설치/권한과 제한은 `CHROME_MESSAGE_CONNECTION.md`를 따른다.

## 지원 준비·메시지 추가 계약 (2026-10-16)

`GET /v1/jobs/applications?limit=1..100`와 단건 GET은 최신순 후보/보류 사유/질문/초안을 반환한다. POST `/{lead_id}/prepare`는 기존 초안을 보존하고 PATCH `/{lead_id}/draft`의 `{text,expected_text}`는 텍스트 비교 후 저장한다. 제출 POST `/{lead_id}/submit`은 항상 409 `application_submission_unavailable`, details.sent=0이다. mode=draft_only이며 외부 제출 클라이언트는 없다.

`GET /v1/jobs/messages/status`, POST `/start` `{confirm:true}`, POST `/stop`은 사용자 시작형 전용 Edge 감시를 제어한다. 최초 상태는 stopped이며 조회만으로 브라우저를 열지 않는다. 로그인은 직접 진행하고 표시 카운터를 2초마다 읽는다. state는 stopped/starting/login_required/watching/surface_unrecognized/error, unread_count의 null은 미확인이며 0과 다르다. 첫 관찰/재연결 기준값 이후 증가만 `daangn_message`로 알린다. `notifications.daangn_messages` 기본 true, Windows 선택·조용한 시간은 유지한다. 본문·발신자 저장/채팅방 열기/자동 지원은 없다. 재시작 시 사용자가 감시를 다시 시작한다.

해당 계약만 갱신할 때는 `scripts/gen-contracts.sh --jobs-only`를 사용한다. 합성 API로 새 fixture·OpenAPI·타입을 생성하고 기존 호환성 fixture는 보존한다.

## 리드 발견 개선 추가 계약 (2026-10-16)

`queue=fresh`는 최근 24시간의 정확한 게시 시각·열린 구매 제작 의뢰·확인 TTL 유효·진행 전 조건이다. 정렬을 생략하면 fresh는 published, 나머지는 priority다. 목록·집계·커서 조건을 일치시킨다. 기존 확인 필요 전체 목록은 유지한다.

추가 필드는 `LeadSummary.freshness`(발견 지연·목록 최초 관찰·목록→상세 지연), `SourceOut.monitoring`(최근 시작/종료·다음 예정·지연·직전 공백), `MetricsOut.freshness`(최근 7일 저장 집단의 지연·분모), `BootstrapOut.notices`(최근 24시간 영속 야간 요약)다. 미확인은 null이며 기존 fixture용 기본값을 제공한다. 요약 건수는 현재 유효 공고 수가 아니다.

`notifications.new_review` 기본 true, 알림 종류 `new_review_lead`를 추가한다. 조용한 시간의 새 알림은 영속 보류하고 앱 내부 요약은 notify=false로 네이티브 중복을 만들지 않는다. claim 정보는 SSE에 포함하지 않는다. 실제 지원·채팅 발송 API는 추가하지 않았다. 소스 주기 최솟값은 30분이다.

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
