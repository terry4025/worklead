# 새 소스 추가 가이드

공통 엔진(HTTP·예산·robots·큐·중복·점수·알림)은 그대로 두고, 사이트 고유 부분만 추가한다. 화면·점수 코드는 수정하지 않는다.

## 1. 먼저 조사

`docs/SOURCE_RESEARCH.md` 형식으로 약관·robots.txt·검색/상세 구조를 확인하고 기록한다. 확인하지 못한 기능은 `unverified`/`unsupported` 로 선언하고 가짜 결과를 만들지 않는다. 선택자·숨은 API·지역 ID 를 추측하지 않는다.

## 2. 방법 A — 조사 프로필만으로 (권장)

검색 URL 에 검색어·지역·페이지를 넣을 수 있고, 목록에서 상세 링크를 찾을 수 있고, 상세에 schema.org `JobPosting` JSON-LD 가 있으면 코드 없이 연결된다.

```
backend/worklead/sources/<site>/
  __init__.py      # create() → ProfiledSiteAdapter(load_profile(...), name=..., scope_note=..., ...)
  profile.json     # 조사 결과 (형식은 backend/tests/fixture_site.py 의 profile() 참고)
```

`profile.json` 핵심 필드

| 필드 | 뜻 |
| --- | --- |
| `verified` | 조사 근거를 문서에 남긴 뒤에만 true |
| `allowed_hosts` | 요청 허용 호스트 (리다이렉트도 검사) |
| `request_budget_per_day`, `min_interval_seconds` | 호스트별 일일 예산·간격 (정책이 더 엄격하면 그에 맞춤) |
| `search.url_template` / `nationwide_url_template` | `{query}`, `{region}`, `{page}` 치환 |
| `search.query_mode` | `per_keyword` 또는 `joined` (OR 검색이 확인된 경우) |
| `regions.items[]` | `{param, label, sido}` — 공개적으로 확인된 지역만. `status: verified` 는 전체성까지 확인한 경우 |
| `list.detail_link_pattern`, `post_id_pattern` | 상세 링크·공고 ID 정규식 |
| `list.empty_markers` | "결과 없음" 문구 (없으면 빈 목록을 파싱 실패로 기록) |
| `detail.strategy` | `jsonld_jobposting` |
| `detail.closed_markers`, `open_markers`, `valid_through_means_open`, `deletion_reliable`, `block_markers` | 조사로 확인된 상태 판단 근거 |
| `capabilities` | 기능 선언 (supported/unsupported/unverified + 메모) |

## 3. 방법 B — 전용 어댑터

프로필로 표현할 수 없으면 `sources/base.py` 의 `SourceAdapter` 계약을 구현한다 (예: `sources/daangn/adapter.py` — 공식 사이트맵 탐색 + 페이지 내 공고 데이터 해석): `describe_capabilities`, `healthcheck`, `plan_discovery`, `discover`, `fetch_detail`, `parse`, `revalidate`, `block_detector`. 모든 요청은 전달받은 `Fetcher` 로만 한다 (직접 HTTP 금지). 어댑터는 UI·영업 상태를 바꾸지 않는다.

## 4. 등록

`sources/registry.py: default_adapters()` 에 추가한다. 원격 코드 플러그인은 지원하지 않는다. 새 사이트 소스의 정책은 자동으로 `permission_pending` 으로 시작한다.

## 5. 테스트

- 허용 범위에서 저장한 표본(개인정보 제거) 또는 합성 HTML 을 `httpx.MockTransport` 로 제공한다 (`tests/fixture_site.py` 참고). CI 에서 실제 사이트를 호출하지 않는다.
- 최소: 정상 목록·페이지 이동·빈 결과·구조 변경(파싱 실패)·403·429·상세 파싱·재확인.
- `uv run pytest` 전체 통과 확인. 화면 코드는 바뀌지 않아야 한다 (소스 목록은 데이터 기반으로 렌더링).
