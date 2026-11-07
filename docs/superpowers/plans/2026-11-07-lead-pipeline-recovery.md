# Worklead 리드 파이프라인 복구 계획 (당근알바·아이보스 → 자동 지원)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 당근알바·아이보스에서 재택 프리랜서 개발 건을 빠르게 찾아오고, 사업자 등록을 반영한 자기소개로 자동 지원까지 이어지게 한다. 이 문서의 상세 작업(1단계)은 11-04부터 멈춘 당근 수집을 복구하고, 같은 장애가 다시 조용히 지나가지 않도록 알림을 붙인다.

**Architecture:** 당근은 페이지 데이터 형식만 바꿨다(`window.__RELAY_STORE__` JSON → TanStack Start 스트림 안 seroval 리터럴 `dehydratedData.relayRecords`). Relay 레코드 구조와 화면 DOM은 그대로라서, 새 형식을 같은 dict로 풀어 주는 디코더 하나를 `_Document.store()` 뒤에 꽂으면 기존 상세·피드 로직이 그대로 동작한다. 실행 결과에서 해석 실패가 성공보다 많으면 하루 한 번 `source_issue` 알림을 보낸다.

**Tech Stack:** Python 3.11 (`backend/.venv`), stdlib `re`/`html.parser`, pytest, httpx MockTransport, SQLAlchemy, Tauri 데스크톱 셸. 새 의존성 없음.

**Spec:** 이 문서의 「1. 목표」와 「2. 현재 상태 진단」이 스펙이다(별도 스펙 문서 없음). 근거 데이터는 2026-11-07 설치본 DB(`%LOCALAPPDATA%\Worklead\worklead.sqlite3`, 읽기 전용 조회)와 `logs\backend.log`, 공개 페이지 실측에서 나왔다.

---

## 1. 목표 (사용자가 원하는 것)

1. 당근알바·아이보스 같은 곳에서 **내가 할 수 있는 재택·온라인 프리랜서 개발 건**을 찾는다.
2. **게시 직후** 찾는다. 지금처럼 하루 가까이 지난 뒤가 아니라.
3. 조건이 맞으면 **자동으로 지원**한다.
4. 당근알바는 **사업자 등록을 마쳤으니** 지원할 때 들어가는 자기소개(이력서) 내용을 바꾼다.

## 2. 현재 상태 진단 (근거 포함)

| # | 심각도 | 문제 | 근거 |
|---|---|---|---|
| P1 | 치명 | **당근 수집이 11-04 20:33(KST)부터 완전히 멈춤.** 지역 피드 99곳과 상세 페이지가 모두 해석 실패 | 마지막 상세 성공 `2026-11-04 11:33 UTC`. `collection_tasks` 실패 4,153건이 모두 `parse_error: 공개 HTML의 공고 데이터를 읽을 수 없습니다`. 상세 `parse_error` 562건. 11-07 실행은 요청 142회, 저장 0건 |
| P1 원인 | — | 당근 웹이 TanStack Start로 바뀌며 `window.__RELAY_STORE__`가 사라지고 `<script data-tsr-stream-part>` 안 seroval 식으로 같은 Relay 레코드를 내보냄. h1·본문 `p.whitespace-pre-wrap`·address·보수 메타는 그대로 | 2026-11-07 실측. 시험용 디코더를 붙이면 기존 어댑터가 상세(제목·보수·상태·게시 시각·주소)를 정상 해석하고 피드 20건도 읽음 |
| P2 | 치명 | **장애 알림이 없음.** 해석 실패는 화면 상태만 `degraded`로 바뀌고, `source_issue` 알림은 접근 차단(`PolicyStop`)일 때만 감 | `engine/executors.py:225-232`(차단만 알림), `:738`(degraded만 기록). 3일간 알림 0건 |
| P3 | 높음 | 새 피드 카드에는 `salaryType`이 없음 → 디코더만 고치면 **건당 우선순위와 월급 공고 강등이 조용히 꺼짐** | 실측 피드 20건 모두 우선순위 0. 카드에는 `stringifiedSalary`("건당 …", "시급 …", "월급 …")만 있음 |
| P4 | 높음 | **느리고 늦음.** 요청 간격 6초 고정, 앵커 99곳 한 바퀴 약 10분 이상, A등급 30분·C등급 120분, 피드 반경 밖은 12시간 사이트맵에 의존. 앱·PC가 꺼진 시간은 수집 공백 | 10-25 이후 당근 808건: 게시→발견 평균 **16.9시간**, 1시간 안 213건(26%), 24시간 초과 196건(24%). 아이보스 180분 주기, 평균 7.4시간. 11-07은 17:45(KST)부터만 실행 기록 있음 |
| P5 | 높음 | **분석 기준이 너무 엄격해서 추천이 0건.** 시급·일급·월급·시간제·정규직·인력 채용이면 개발 의뢰여도 무조건 제외 | 전체 2,519건 중 추천 0, 검토 15(전부 당근). 예: "노션과 불렛 활용 홈페이지 제작" 적합도 82인데 시급이라 제외, "아임웹 홈페이지만들기" 제외, "동탄 2~3개월 AI 활용 프로그램 개발"(재택 명시)은 월급이라 제외. `analysis/service.py:379-414` |
| P6 | 높음 | 제목 필터 잡음: `자동화`가 물류자동화·자동화설비·생산라인을 잡아 6초짜리 상세 조회를 낭비 | 11-03~04 개발 키워드 제목 40건 중 공장·물류 일이 10건 이상 |
| P7 | 높음 | **아이보스는 마케팅 대행 의뢰 게시판(`/ab-1957`)을 긁고 있음.** 개발 건이 거의 없고, "월간 최소 N만원" 예산을 월급으로 해석해 제외. 연락처는 유료 정회원 열람이라 자동 지원 경로 없음 | 최근 30건 중 개발은 "홈페이지 유지보수" 1건. `pay_raw` 상위 15종이 모두 "월간…". 393건 전부 제외 |
| P8 | 높음 | **자동 지원 성공 0건.** 시도 6건: 이미 지원 3, `ui_unrecognized` 2(11-03, 화면 개편 시점), 오래됨 1. 확장의 화면 인식 문구도 바뀌었을 가능성 큼(로그인이 필요해 미검증) | `job_auto_attempts`. `extensions/worklead-messages/auto-content.js:33,68` |
| P9 | 중간 | 자동 지원 조건이 "사업자 등록 필수/세금계산서 발행" 공고를 **충돌로 막음**. 사업자 등록 후에는 오히려 유리한 공고 | `jobs/auto_apply.py:78` |
| P10 | 중간 | 지원서 내용은 앱이 쓰지 않음. 확장은 **당근 계정에 저장된 "기본" 자기소개를 그대로 제출**하고 해시로 고정(바뀌면 `profile_changed`로 정지). 앱의 `user_profile.skills`는 빈 값 | `auto-content.js:66-70`, `user_profile` 행 |
| P11 | 중간 | 소스 관리: 사본 3곳. 실제 작업본 `C:\Users\Administrator\.gemini\antigravity\scratch\worklead`(브랜치 `feat/daangn-region-feed`)에 미커밋 변경 67개(+1301/−162, 새 파일 24), 마지막 커밋 10-26. 설치본(11-02)의 정확한 빌드 소스와 증거 폴더(`artifacts/install-20261102-*`)는 디스크에 없음. 미배포 "M4 분류 변경"이 작업 트리에 섞여 있음. 텔레그램 알림(`4b65387`)은 `feat/apply-fast-path`에만 있음 | `git status`, `git merge-base` |
| P12 | 낮음 | `daangn_cafe` 163건도 전부 제외, DB 388MB + WAL 119MB | DB 집계 |

**결론:** 지금 가장 큰 문제는 "느림"이 아니라 **3일째 아무것도 못 가져오고 있는데 아무도 모르는 상태**다(P1+P2). 복구해도 분석 기준(P5)과 아이보스 대상(P7) 때문에 추천이 0건이라 자동 지원(P8)이 돌 대상이 없다. 그래서 순서는 **복구 → 알림 → 분석 기준 → 신선도 → 사업자 반영 지원 → 아이보스**다.

## 3. 단계 로드맵

| 단계 | 내용 | 사용자 결정 | 완료 기준 |
|---|---|---|---|
| 0 | 작업본 스냅샷, 기준 테스트 기록 | 없음 | 미커밋 작업이 커밋으로 보존되고 기준 테스트 결과가 남음 |
| **1** | **당근 수집 복구 + 구조 변경 알림 (이 문서에 상세 작업)** | 없음 | 단위 테스트 통과, 실제 페이지 점검 통과, 설치 후 새 공고 저장 재개 |
| 2 | 분석 기준 재설계 (재택 개발이면 지급 방식으로 일괄 제외하지 않음, 공장 자동화 제외) | Q1 | 복사본 DB 재분석에서 P5 예시들이 검토 이상, 물류·현장 일은 계속 제외 |
| 3 | 신선도: 낭비 요청 줄이기, 게시→발견 지표, 키워드 질의 경로 검증 | Q4 | 앱 가동 3일 기준 게시→발견 중앙값과 90% 지점이 화면에 표시되고 1단계 직후보다 줄어듦 |
| 4 | 사업자 반영: 조건 반전, 프로필 필드, 당근 자기소개 문안 교체 | Q3 | 세금계산서 요구 공고가 지원 후보에 들어가고, 바뀐 자기소개로 재연결됨 |
| 5 | 자동 지원 화면 재검증 (로그인된 전용 Edge, 제출 버튼은 누르지 않고 점검) | Q5 | 실제 공고에서 점검 결과 `ready`, 첫 실제 자동 제출을 `지원 취소` 표시로 확인 |
| 6 | 아이보스 방향 정리 | Q2 | 선택안대로 대상·예산 해석이 바뀜 |
| 7 | 운영: 텔레그램 알림 병합, DB 정리 | 없음 | 휴대폰으로 구조 변경·신규 추천 알림 수신 |

### 사용자 결정 질문

- **Q1 지급 방식:** 당근알바는 글쓴이가 시급·일급·건당 중 하나를 고르게 되어 있어서, 짧은 개발 의뢰도 시급으로 올라오는 일이 많다. 재택 개발 일이면 시급·일급·기간제 월급이어도 후보(검토)로 올릴까? 권장: 올리되 점수를 깎고 "시급 표기 — 실제 작업 단위 확인" 표시, 자동 지원은 건당과 단기 시급만.
- **Q2 아이보스:** (A) 개발 분류만 남기고 알림 전용으로 유지(자동 지원 불가) (B) 중단하고 개발 의뢰가 많은 다른 곳(위시켓·크몽 등, 각 약관 검토 필요)을 별도 프로젝트로 검토 (C) 지금처럼 유지. 권장: A를 바로 하고 B는 따로 검토.
- **Q3 자기소개 문안:** 공개할 사업자 정보 범위(상호, 세금계산서·현금영수증 발행 가능 여부), 주력 기술, 포트폴리오 링크.
- **Q4 요청 간격:** 6초를 유지할까? 권장: 유지. 속도는 낭비 요청을 줄여서 얻는다(차단 위험 때문).
- **Q5 하루 지원 상한:** 현재 20건. 유지할까?

---

## Global Constraints

- 요청 간격 6초, 하루 예산 당근 5,500회·아이보스 600회, 호스트 cooldown은 이 계획에서 바꾸지 않는다(Q4 결정 전).
- 로그인 우회, 차단 우회, 유료 연락처 열람, 페이지 스크립트 실행은 하지 않는다. 내려받은 문서는 데이터로만 해석한다.
- 자동 지원은 당근 계정에 저장된 기본 자기소개만 제출하고, 앱이 입력란을 채우거나 고치지 않는다(기존 설계 유지).
- 설치된 사용자 DB는 직접 고치지 않는다. 검증은 복사본이나 테스트 DB에서 한다. 설치본 교체는 사용자 확인 후에만 한다.
- 코드 식별자·커밋 메시지는 영어, 문서·사용자 문구는 한국어.
- 테스트 실행: `backend` 폴더에서 `.venv\Scripts\python.exe -m pytest <파일> -p no:cacheprovider -W ignore`.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`을 붙인다.

## Review Focus

1. 제목·본문에 이모지가 서로게이트 쌍 이스케이프(`\ud83d\ude00`)로 들어온 공고: 깨지지 않고 한 글자로 저장돼야 한다 → Task 1 `test_string_escapes_and_surrogate_pairs`.
2. 스트림이 여러 조각으로 오거나, 라우터가 아닌 조각이 먼저 올 때: 라우터 조각을 찾아 읽어야 한다. 레거시 형식도 계속 읽어야 한다 → Task 1 `test_skips_non_router_stream_parts`, Task 2는 기존 58개 테스트 유지.
3. 스트림에 함수·호출처럼 해석하지 않는 문법이 생길 때: 추측하지 말고 `ParseError`를 내고 알림으로 이어져야 한다 → Task 2 `test_current_stream_with_code_is_not_guessed`, Task 4.
4. 보수 표기가 "협의"이거나 비어 있을 때: 오류 없이 우선순위 0, 강등 없음 → Task 3 `test_salary_type_prefers_field_then_visible_label`.
5. 하루에 여러 번 구조 변경 실패가 나도 알림은 하루 한 번(조용한 시간에는 보류) → Task 4 `test_structure_change_alerts_once_per_day`.

---

## 1단계 상세 작업

### 파일 구조

| 파일 | 역할 |
|---|---|
| Create `backend/worklead/sources/daangn/serialized.py` | TanStack 스트림에서 Relay 레코드 dict를 꺼내는 디코더(리터럴 부분집합만) |
| Modify `backend/worklead/sources/daangn/adapter.py` | `_Document.store()` 레거시 실패 시 디코더 사용, 피드 보수 종류를 표시 문구로 보완 |
| Modify `backend/worklead/engine/executors.py` | 실행 결과의 해석 실패가 많으면 하루 한 번 `source_issue` 알림 |
| Create `backend/tests/test_daangn_serialized.py` | 디코더 단위 테스트와 새 형식 테스트 도우미(`js`, `tsr_script`, `as_tsr`) |
| Modify `backend/tests/test_daangn.py`, `backend/tests/test_daangn_feed.py` | 새 형식 상세·피드·알림 회귀 테스트 |

### Task 0: 작업본 스냅샷과 기준선

**Files:** 없음(git 작업만)

- [ ] **Step 1: 미커밋 작업을 스냅샷 브랜치에 보존**

```bash
cd "C:/Users/Administrator/.gemini/antigravity/scratch/worklead"
git switch -c snapshot/2026-11-07-wip
git add -A
git commit -m "chore: snapshot uncommitted work before daangn recovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git switch -c fix/daangn-tsr-store
```

- [ ] **Step 2: 당근 테스트 기준선 확인**

Run (PowerShell, `backend` 폴더): `.venv\Scripts\python.exe -m pytest tests/test_daangn.py tests/test_daangn_feed.py -p no:cacheprovider -W ignore`
Expected: `58 passed` (2026-11-07 확인값)

- [ ] **Step 3: 전체 백엔드 기준선을 파일 단위로 기록** (기존 문서가 파일 단위 실행을 기준으로 함)

```powershell
foreach ($f in Get-ChildItem tests -Filter 'test_*.py') { $last = & .venv\Scripts\python.exe -m pytest $f.FullName -p no:cacheprovider -W ignore -q 2>&1 | Select-Object -Last 1; "$($f.Name): $last" } | Tee-Object ..\docs\superpowers\plans\baseline-20261107.txt
```

Expected: 파일별 결과가 기록됨. 실패가 있으면 1단계와 무관한 기존 실패로 표시하고 진행(M4 미배포 변경 포함 여부 확인).

### Task 1: TanStack 스트림 디코더

**Files:**
- Create: `backend/worklead/sources/daangn/serialized.py`
- Test: `backend/tests/test_daangn_serialized.py`

**Interfaces:**
- Produces: `relay_records(markup: str) -> dict` (Relay 레코드, `"client:root"` 포함 보장), `SerializedError(ValueError)`.
- Produces (테스트 도우미): `js(value) -> str`, `tsr_script(store: dict) -> str`, `as_tsr(markup: str) -> str`.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_daangn_serialized.py`:

```python
"""TanStack Start stream decoding, shaped like jobs.daangn.com on 2026-11-07."""
import json
import re

import pytest

from worklead.sources.daangn.serialized import SerializedError, _Reader, relay_records

_KEY = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def js(value) -> str:
    """Render test data the way seroval does (literal subset only)."""
    if value is True:
        return "!0"
    if value is False:
        return "!1"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(js(v) for v in value) + "]"
    return "{" + ",".join((k if _KEY.fullmatch(k) else json.dumps(k, ensure_ascii=False)) + ":" + js(v)
                          for k, v in value.items()) + "}"


def tsr_script(store: dict) -> str:
    """One router stream part carrying `store` as dehydrated relay records."""
    return ('<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={manifest:$R[1]={routes:$R[2]={}},'
            'matches:$R[3]=[$R[4]={i:"__root__",s:"success",l:$R[5]={},ssr:!0}],'
            f'dehydratedData:$R[6]={{relayRecords:$R[7]={js(store)}}}}})($R["tsr"])</script>')


def as_tsr(markup: str) -> str:
    """Replace a legacy window.__RELAY_STORE__ script with the current stream form."""
    from worklead.sources.daangn.adapter import _Document
    store = _Document(markup).store()
    legacy = re.compile(r"<script[^>]*>\s*window\.__RELAY_STORE__.*?</script>", re.S)
    assert legacy.search(markup), "fixture has no legacy store"
    return legacy.sub(lambda _: tsr_script(store), markup, count=1)


def wrap(body: str) -> str:
    return '<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={dehydratedData:{' + body + '}})($R["tsr"])</script>'


def test_literal_subset():
    text = '$R[0]={a:!0,"b c":[1,-2.5,null,void 0],d:"x",e:$R[1]={f:!1},g:$R[1]}'
    assert _Reader(text, 0, {}).value() == {"a": True, "b c": [1, -2.5, None, None], "d": "x",
                                            "e": {"f": False}, "g": {"f": False}}


def test_string_escapes_and_surrogate_pairs():
    assert _Reader('"a\\nb\\u00e9\\ud83d\\ude00\\x41\\u{1F600}"', 0, {}).value() == "a\nbé😀A😀"


@pytest.mark.parametrize("bad", ['alert(1)', 'function(){}', '"unterminated', '$R[9]', '{a:1'])
def test_rejects_code_truncation_and_unknown_slots(bad):
    with pytest.raises(SerializedError):
        relay_records(wrap(f"relayRecords:$R[1]={bad}"))


def test_back_reference_defined_before_relay_records():
    script = ('<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={matches:$R[1]=[$R[2]={l:$R[3]={workDays:$R[4]=["MON"]}}],'
              'dehydratedData:$R[5]={relayRecords:$R[6]={"client:root":$R[7]={__id:"client:root"},job:$R[8]={workDays:$R[4]}}}})($R["tsr"])</script>')
    assert relay_records(script)["job"]["workDays"] == ["MON"]


def test_skips_non_router_stream_parts():
    store = {"client:root": {"__id": "client:root"}, "job": {"title": "홈페이지 제작"}}
    markup = ('<script data-tsr-stream-part="">(self.$R=self.$R||{})["tsr"]=[];</script>'
              + tsr_script(store) + '<script data-tsr-stream-part="">$_TSR.e();</script>')
    assert relay_records(markup)["job"]["title"] == "홈페이지 제작"


def test_missing_stream_or_root_is_an_error():
    with pytest.raises(SerializedError):
        relay_records("<html><body><h1>새 화면</h1></body></html>")
    with pytest.raises(SerializedError):
        relay_records(tsr_script({"job": {"title": "x"}}))
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn_serialized.py -p no:cacheprovider -W ignore`
Expected: FAIL — `ModuleNotFoundError: No module named 'worklead.sources.daangn.serialized'`

- [ ] **Step 3: 최소 구현**

`backend/worklead/sources/daangn/serialized.py`:

```python
"""Decode relay records from TanStack Start SSR stream parts.

Since 2026-11-04 jobs.daangn.com no longer embeds window.__RELAY_STORE__.
The same relay record source is serialized by seroval inside
<script data-tsr-stream-part> as a JavaScript expression. Only a literal
subset is decoded: objects, arrays, strings, numbers, !0/!1, null, void 0
and $R[n] slot definitions/references. Calls, functions or any other token
raise SerializedError; nothing is evaluated.
"""
from __future__ import annotations

import re

_SCRIPT = re.compile(r"<script\b[^>]*\bdata-tsr-stream-part\b[^>]*>(.*?)</script>", re.S | re.I)
_ROUTER = re.compile(r"\(\$R=>(?=\$R\[0\]=)")
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_PLAIN = {'"': re.compile(r'[^"\\]*'), "'": re.compile(r"[^'\\]*")}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_LITERALS = (("!0", True), ("!1", False), ("null", None), ("void 0", None))


class SerializedError(ValueError):
    """The stream is missing or uses syntax outside the decoded literal subset."""


class _Reader:
    def __init__(self, text: str, pos: int, slots: dict[int, object]):
        self.text, self.pos, self.slots = text, pos, slots

    def _take(self, token: str) -> bool:
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def value(self) -> object:
        text, pos = self.text, self.pos
        if self._take("$R["):
            end = text.find("]", self.pos)
            if end < 0 or not text[self.pos:end].isdigit():
                raise SerializedError(f"잘못된 참조 (위치 {pos})")
            slot, self.pos = int(text[self.pos:end]), end + 1
            if self._take("="):
                self.slots[slot] = None  # a cycle resolves to None, never recursion
                self.slots[slot] = self.value()
            elif slot not in self.slots:
                raise SerializedError(f"정의되지 않은 참조 $R[{slot}]")
            return self.slots[slot]
        char = text[pos:pos + 1]
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char in _PLAIN:
            return self._string(char)
        for token, literal in _LITERALS:
            if self._take(token):
                return literal
        match = _NUMBER.match(text, pos)
        if match:
            self.pos = match.end()
            raw = match.group()
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        raise SerializedError(f"해석하지 않는 문법 (위치 {pos}): {text[pos:pos + 20]!r}")

    def _object(self) -> dict:
        self.pos += 1
        result: dict = {}
        while not self._take("}"):
            if self.text[self.pos:self.pos + 1] in _PLAIN:
                key = self._string(self.text[self.pos])
            else:
                match = _IDENT.match(self.text, self.pos) or _NUMBER.match(self.text, self.pos)
                if not match:
                    raise SerializedError(f"잘못된 키 (위치 {self.pos})")
                key, self.pos = match.group(), match.end()
            if not self._take(":"):
                raise SerializedError(f"':' 누락 (위치 {self.pos})")
            result[key] = self.value()
            self._take(",")
        return result

    def _array(self) -> list:
        self.pos += 1
        result: list = []
        while not self._take("]"):
            result.append(self.value())
            self._take(",")
        return result

    def _string(self, quote: str) -> str:
        text, pos, out, plain = self.text, self.pos + 1, [], _PLAIN[quote]
        while True:
            end = plain.match(text, pos).end()
            out.append(text[pos:end])
            if end >= len(text):
                raise SerializedError("문자열이 닫히지 않았습니다")
            if text[end] == quote:
                self.pos = end + 1
                # Join UTF-16 surrogate pairs (emoji) escaped as two \\u units.
                return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            code = text[end + 1:end + 2]
            if code == "u" and text[end + 2:end + 3] == "{":
                close = text.index("}", end)
                out.append(chr(int(text[end + 3:close], 16)))
                pos = close + 1
            elif code == "u":
                out.append(chr(int(text[end + 2:end + 6], 16)))
                pos = end + 6
            elif code == "x":
                out.append(chr(int(text[end + 2:end + 4], 16)))
                pos = end + 4
            else:
                out.append(_ESCAPES.get(code, code))
                pos = end + 2


def _find_key(tree: object, key: str) -> object:
    stack, seen = [tree], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            if key in node:
                return node[key]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def relay_records(markup: str) -> dict:
    """Relay records from the router stream part, or SerializedError."""
    slots: dict[int, object] = {}
    for script in _SCRIPT.findall(markup):
        router = _ROUTER.search(script)
        if not router:
            continue
        try:
            tree = _Reader(script, router.end(), slots).value()
        except (IndexError, ValueError, RecursionError) as exc:
            raise SerializedError("TanStack 스트림 데이터를 해석할 수 없습니다") from exc
        records = _find_key(tree, "relayRecords")
        if isinstance(records, dict) and isinstance(records.get("client:root"), dict):
            return records
    raise SerializedError("TanStack 스트림에 relayRecords가 없습니다")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn_serialized.py -p no:cacheprovider -W ignore`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/worklead/sources/daangn/serialized.py backend/tests/test_daangn_serialized.py
git commit -m "feat: decode daangn relay records from TanStack stream parts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: 어댑터가 새 형식을 읽게 하기

**Files:**
- Modify: `backend/worklead/sources/daangn/adapter.py:1-8` (모듈 설명), `:142-152` (`_Document.__init__`), `:178-190` (`store`)
- Test: `backend/tests/test_daangn.py`, `backend/tests/test_daangn_feed.py`

**Interfaces:**
- Consumes: `relay_records`, `SerializedError` (Task 1), `as_tsr` (Task 1 테스트 도우미).
- Produces: `_Document.store()`는 레거시와 새 형식 모두에서 같은 Relay dict를 돌려준다. `_Document.markup: str` 속성 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_daangn.py` 맨 아래에 추가:

```python
from tests.test_daangn_serialized import as_tsr


def _fields(post):
    return (post.title, post.body, post.pay.min, post.pay.unit, post.published.at,
            post.source_status, post.workplace_raw, post.source_post_id)


def test_current_stream_detail_matches_legacy_fields():
    legacy = create().parse(PostRef(URL, "fixture00001"), response(markup("detail")), NOW)
    current = create().parse(PostRef(URL, "fixture00001"), response(as_tsr(markup("detail"))), NOW)
    assert _fields(current) == _fields(legacy)


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda d: d["job"].update(hidden=True), "hidden_or_deleted"),
    (lambda d: d["user"].update(webCrawlNotAllowed=True), "collection_disallowed"),
    (lambda d: d["user"].pop("webCrawlNotAllowed"), "collection_permission_unknown"),
])
def test_current_stream_keeps_collection_permission_gate(mutate, code):
    with pytest.raises(PostNotCollectable) as caught:
        create().parse(PostRef(URL), response(as_tsr(changed(markup("detail"), mutate))), NOW)
    assert caught.value.code == code


def test_current_stream_with_code_is_not_guessed():
    broken = as_tsr(markup("detail")).replace("relayRecords:$R[7]=", "relayRecords:$R[7]=alert(1),x:")
    with pytest.raises(ParseError):
        create().parse(PostRef(URL), response(broken), NOW)
```

`backend/tests/test_daangn_feed.py` 맨 아래에 추가:

```python
from tests.test_daangn_serialized import as_tsr


def test_current_stream_feed_matches_legacy():
    adapter = create()
    legacy = adapter.discover(feed_task(adapter), Static(feed(28, POSTS)))
    adapter = create()
    current = adapter.discover(feed_task(adapter), Static(as_tsr(feed(28, POSTS))))
    assert [r.source_post_id for r in current.refs] == [r.source_post_id for r in legacy.refs]
    assert [r.source_post_id for r in current.exploration_refs] == [r.source_post_id for r in legacy.exploration_refs]
    assert [r.priority for r in current.refs] == [r.priority for r in legacy.refs]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn.py tests/test_daangn_feed.py -p no:cacheprovider -W ignore -k current_stream`
Expected: FAIL — `ParseError: 공개 HTML의 공고 데이터를 읽을 수 없습니다 (구조 변경 가능성)` (단, `test_current_stream_with_code_is_not_guessed`는 이미 통과할 수 있음)

- [ ] **Step 3: 최소 구현**

`adapter.py` import 줄 추가(`from ..htmlparse import ...` 아래):

```python
from .serialized import SerializedError, relay_records
```

모듈 설명 1~8줄을 교체:

```python
"""Anonymous public HTML adapter, verified against the 2026-09-28 site.

Region feeds (/s?regionId=, first 20 only) were verified on 2026-10-17;
the web client fixes regionRange and pages only through GraphQL.
Since 2026-11-04 the relay records arrive in TanStack Start stream parts
instead of window.__RELAY_STORE__ (see serialized.py); the DOM is unchanged.

Only the downloaded document is parsed. Embedded data is decoded as data,
never evaluated; no GraphQL, login session or pagination API is requested.
"""
```

`_Document.__init__` 첫 줄에 원문 보관:

```python
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.markup = markup
```

`store()`의 마지막 `raise` 앞에 새 형식 시도를 넣는다:

```python
            if isinstance(data, dict) and isinstance(data.get("client:root"), dict):
                return data
        try:
            return relay_records(self.markup)
        except SerializedError:
            pass
        raise ParseError("공개 HTML의 공고 데이터를 읽을 수 없습니다 (구조 변경 가능성)")
```

- [ ] **Step 4: 통과 확인 (기존 58개 포함)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn.py tests/test_daangn_feed.py tests/test_daangn_serialized.py -p no:cacheprovider -W ignore`
Expected: PASS (58 + 6 + 10 = 74 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/worklead/sources/daangn/adapter.py backend/tests/test_daangn.py backend/tests/test_daangn_feed.py
git commit -m "fix: read daangn relay records from TanStack stream when legacy store is gone

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: 피드 보수 종류를 표시 문구로 보완

**Files:**
- Modify: `backend/worklead/sources/daangn/adapter.py` (`_post_slug` 위에 함수 추가, `_discover_feed`의 `salary = post.get("salaryType")` 줄)
- Test: `backend/tests/test_daangn_feed.py`

**Interfaces:**
- Consumes: `tsr_script` (Task 1 테스트 도우미), `_Document.store()` (Task 2).
- Produces: `_salary_type(post: dict) -> str | None` — `"PER_CASE" | "HOURLY" | "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY" | None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_daangn_feed.py` 맨 아래에 추가:

```python
from tests.test_daangn_serialized import tsr_script
from worklead.sources.daangn.adapter import _Document, _salary_type

LABELS = {"PER_CASE": "건당 50,000원", "HOURLY": "시급 12,000원", "MONTHLY": "월급 2,500,000원"}


@pytest.mark.parametrize(("post", "kind"), [
    ({"salaryType": "HOURLY", "stringifiedSalary": "건당 1원"}, "HOURLY"),
    ({"stringifiedSalary": "건당 50,000원"}, "PER_CASE"),
    ({"stringifiedSalary": " 월급 2,500,000원"}, "MONTHLY"),
    ({"stringifiedSalary": "협의"}, None),
    ({}, None),
])
def test_salary_type_prefers_field_then_visible_label(post, kind):
    assert _salary_type(post) == kind


def test_current_feed_without_salary_type_uses_visible_label():
    store = _Document(feed(28, POSTS)).store()
    for node in store.values():
        if isinstance(node, dict) and node.get("__typename") == "JobPost":
            node["stringifiedSalary"] = LABELS[node.pop("salaryType")]
    adapter = create()
    found = adapter.discover(feed_task(adapter), Static(f"<html><body>{tsr_script(store)}</body></html>"))
    assert [r.source_post_id for r in found.refs] == ["feedcore0001", "feedcore0002"]
    assert [r.source_post_id for r in found.exploration_refs] == ["feedjobs0003", "feedexpl0005"]
    assert [r.priority for r in found.refs] == [1, 0]
    meta = found.metadata
    assert (meta["employment_exploration_filtered"], meta["employment_core_demoted"]) == (1, 1)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn_feed.py -p no:cacheprovider -W ignore -k "salary_type or visible_label"`
Expected: FAIL — `ImportError: cannot import name '_salary_type'`

- [ ] **Step 3: 최소 구현**

`adapter.py`의 `_post_slug` 바로 위에 추가:

```python
# Feed cards dropped salaryType on 2026-11-04; the visible label still says it.
_SALARY_LABELS = (("건당", "PER_CASE"), ("시급", "HOURLY"), ("일급", "DAILY"),
                  ("주급", "WEEKLY"), ("월급", "MONTHLY"), ("연봉", "YEARLY"))


def _salary_type(post: dict) -> str | None:
    if post.get("salaryType"):
        return post["salaryType"]
    label = normalize_text(str(post.get("stringifiedSalary") or ""))
    return next((kind for prefix, kind in _SALARY_LABELS if label.startswith(prefix)), None)
```

`_discover_feed`에서:

```python
            salary = _salary_type(post)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn.py tests/test_daangn_feed.py tests/test_daangn_serialized.py -p no:cacheprovider -W ignore`
Expected: PASS (80 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/worklead/sources/daangn/adapter.py backend/tests/test_daangn_feed.py
git commit -m "fix: derive daangn feed salary type from the visible label

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: 구조 변경 알림

**Files:**
- Modify: `backend/worklead/engine/executors.py` (`_set_health` 아래에 함수 추가, `:738` `_set_health(...)` 다음 줄에서 호출)
- Test: `backend/tests/test_daangn.py`

**Interfaces:**
- Consumes: `notify(s, kind, title, *, dedupe_key, source_id, now)` (`engine/events.py:100`), `scope.counts` 키 `parse_failures`, `details_fetched`.
- Produces: `_alert_structure_change(app, source_id: str, counts: dict) -> None`, 상수 `STRUCTURE_ALERT_MIN_PARSE_FAILURES = 3`.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_daangn.py` 맨 아래에 추가:

```python
from sqlalchemy import select
from worklead.models import EventOutbox


def _source_issues(ctx):
    with ctx.db.session() as s:
        return [row.payload for row in s.scalars(select(EventOutbox).where(EventOutbox.type == "notification.created"))
                if row.payload.get("kind") == "source_issue"]


def test_structure_change_alerts_once_per_day(tmp_path):
    adapter = sitemap_only()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    urls = [URL.replace("fixture00001", f"fixture0000{i}") for i in range(1, 4)]
    jobs = f"<urlset {NS}>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith("/sitemaps/"):
            return httpx.Response(200, text=jobs)
        return httpx.Response(200, text="<html><body><h1>새 화면</h1></body></html>")

    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "restricted", "basis": "합성 표본 테스트"}})
    c.patch("/v1/settings", json={"query_groups": [{"id": "dev", "label": "개발", "enabled": True, "keywords": ["프로그램"]}]})
    for _ in range(2):
        c.post("/v1/runs", json={"kind": "discovery", "source_id": "daangn-alba"})
        Worker(ctx, "test", runs.COLLECTION_KINDS).run_once()
    notes = _source_issues(ctx)
    assert len(notes) == 1 and "구조" in notes[0]["title"]
    ctx.db.dispose()
```

같은 파일 `test_adapter_through_collection_worker_and_dedup`의 `ctx.db.dispose()` 바로 위에 정상 실행은 알리지 않는다는 확인을 추가:

```python
    assert _source_issues(ctx) == []
```

(`_source_issues`는 파일 아래쪽에 정의되지만 테스트 실행 시점에는 모듈 전체가 로드되어 있으므로 문제없다.)

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn.py -p no:cacheprovider -W ignore -k "structure_change or worker_and_dedup"`
Expected: `test_structure_change_alerts_once_per_day` FAIL — `assert 0 == 1`; `test_adapter_through_collection_worker_and_dedup` PASS

- [ ] **Step 3: 최소 구현**

`executors.py`의 `_set_health` 함수 바로 아래에 추가:

```python
STRUCTURE_ALERT_MIN_PARSE_FAILURES = 3


def _alert_structure_change(app: AppContext, source_id: str, counts: dict) -> None:
    """More parse failures than parsed pages means the site layout changed; say so once a day."""
    failures = int(counts.get("parse_failures", 0))
    if failures < STRUCTURE_ALERT_MIN_PARSE_FAILURES or failures <= int(counts.get("details_fetched", 0)):
        return
    now = app.clock()
    with app.db.session() as s:
        source = s.get(Source, source_id)
        if source is None:
            return
        notify(s, "source_issue", f"{source.name}: 페이지 구조가 바뀌어 공고를 읽지 못합니다 (해석 실패 {failures}건)",
               dedupe_key=f"notify:source:{source_id}:structure:{now.date().isoformat()}",
               source_id=source_id, now=now)
    app.hub.notify()
```

`:738`의 `_set_health(app, run.source_id or "", "degraded" if has_errors else "ok", message or skip_note)` 바로 다음 줄:

```python
    _alert_structure_change(app, run.source_id or "", scope.counts)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daangn.py tests/test_daangn_feed.py tests/test_daangn_serialized.py tests/test_engine.py -p no:cacheprovider -W ignore`
Expected: PASS (당근 81개 + `test_engine.py` 기존 전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/worklead/engine/executors.py backend/tests/test_daangn.py
git commit -m "feat: notify once a day when a source's pages stop parsing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: 실제 페이지 점검, 전체 검사, 설치(사용자 확인 후)

**Files:** 저장소 변경 없음(점검 스크립트는 스크래치 폴더에 두고 커밋하지 않음). 결과는 `docs/STATUS.md`에 한 문단 기록.

- [ ] **Step 1: 전체 백엔드 검사를 파일 단위로 실행하고 Task 0 기준선과 비교**

Task 0 Step 3과 같은 명령. Expected: 기준선 대비 새 실패 0, 당근 관련 파일은 모두 통과.

- [ ] **Step 2: 실제 공개 페이지 1회 점검 (상세 1건 + 피드 1곳, 6초 간격)**

스크래치 폴더에 `live_check.py`로 저장 후 `backend\.venv\Scripts\python.exe live_check.py` 실행:

```python
import sys, time
from datetime import UTC, datetime
sys.path.insert(0, r"C:\Users\Administrator\.gemini\antigravity\scratch\worklead\backend")
import httpx
from worklead.engine.http import FetchResult
from worklead.sources.base import PostRef
from worklead.sources.daangn import create

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
FEED = "https://jobs.daangn.com/s?regionId=400&jobTasks=%5B%22ETC%22%2C%22LIGHT_WORK%22%2C%22OFFICE_ASSISTANCE%22%2C%22TUTORING%22%5D"

def get(url):
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
    return FetchResult(url, str(r.url), r.status_code, "ok" if r.status_code == 200 else "http_error", r.text)

class Live:
    def get(self, url, **kwargs):
        return get(url)

adapter = create()
adapter.profile["discovery"]["feed"]["anchors"] = [[400, "일원본동", "A"]]
task = next(t for t in adapter.plan_discovery([{"id": "dev", "keywords": ["프로그램", "홈페이지"]}], datetime.now(UTC)).tasks
            if t.region_scope == "feed-400")
found = adapter.discover(task, Live())
print("feed listed", len(found.listed_refs), "priorities", sorted({r.priority for r in found.listed_refs}))
ref = found.listed_refs[0]
time.sleep(6)
post = adapter.parse(PostRef(ref.url, ref.source_post_id), get(ref.url), datetime.now(UTC))
print("detail ok:", post.source_post_id, post.pay.unit, post.source_status, bool(post.body))
```

Expected: `feed listed 20`, 피드에 건당 공고가 있으면 우선순위 집합에 `1` 포함, `detail ok: … True`. (첫 공고가 수집 비허용이면 `PostNotCollectable`이 날 수 있으므로 그때는 `listed_refs[1]`로 바꿔 한 번 더.)

- [ ] **Step 3: 사용자에게 설치본 교체 확인 받기**

교체 전 백업: 앱 종료 후 `%LOCALAPPDATA%\Worklead\worklead.sqlite3`, `-wal`, `-shm`, `worklead-backend.exe`, `worklead.exe`를 `backups\pre-tsr-20261107\`로 복사. 빌드는 `docs/OPERATIONS.md:33` 절차(`desktop`에서 `npm run tauri build -- --no-bundle` 또는 `scripts\build-windows.ps1`).

- [ ] **Step 4: 설치 후 15분 운영 확인 (DB 읽기 전용 조회)**

```sql
select outcome, count(*) from detail_fetch_states
 where source_id='daangn-alba' and attempted_at > :restart_utc group by 1;
select count(*) from source_records where source_id='daangn-alba' and first_seen_at > :restart_utc;
select state, count(*) from collection_tasks
 where source_id='daangn-alba' and region_scope like 'feed-%' and updated_at > :restart_utc group by 1;
```

Expected: 새 시도에 `parse_error` 0(또는 개별 공고 몇 건), `feed-*` 작업 `done`, 새 공고 저장 1건 이상.

- [ ] **Step 5: 기록과 커밋**

`docs/STATUS.md` 맨 위에 "2026-11-07 당근 TanStack 전환 대응" 한 문단(원인, 바뀐 파일, 실제 점검 결과, 설치 확인 결과)을 추가하고 커밋:

```bash
git add docs/STATUS.md
git commit -m "docs: record daangn TanStack stream recovery and live verification

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 2~7단계 작업 개요 (결정 후 각각 상세 계획으로 작성)

### 2단계: 분석 기준 재설계 (Q1 결정 필요)

- 대상 코드: `backend/worklead/analysis/service.py:379-414`(제외 사유), `analysis/rules.py`, `sources/daangn/adapter.py`의 `title_pattern`·`_PHYSICAL_WORK_TITLE`.
- 방향(Q1 권장안 기준): 개발 관련 + 구매 의뢰 + 상주 필수 아님이면 시급·일급·기간제 월급으로 **일괄 제외하지 않고** 검토로 올리고 점수를 깎는다. 정규직·상주·인력 파견은 계속 제외. 공장 쪽 표현(`물류\s*자동화`, `자동화\s*(?:설비|장비|라인|센터|공장)`, `생산\s*라인`)은 제목 단계에서 걸러 상세 조회를 아낀다.
- 검증: 기존 코퍼스 `tests/fixtures/daangn_m4_corpus.json`과 `tests/test_daangn_m4_corpus.py`에 P5 예시를 추가한다. 설치 DB 복사본을 재분석해 추천·검토·제외 분포를 전후 비교한다.

### 3단계: 신선도

- 먼저 측정: 소스별 게시→발견 중앙값과 90% 지점을 진단 화면(`services/diagnostics.py`)에 보인다. 앱이 꺼져 있던 시간은 따로 표시한다.
- 낭비 줄이기: 2단계 제목 필터로 상세 조회를 줄이고, 건당·개발 제목은 같은 실행 안에서 바로 상세를 확인한다(현재 실행당 기본 4건).
- 경로 검증: 지역 검색 `https://jobs.daangn.com/s?regionId=N&query=홈페이지`는 2026-11-07 실측에서 지역 반경(RANGE_4)에 묶여 있지만 검색어가 반영된다. 직종 필터 피드와 하루 동안 요청당 유효 후보 수를 비교해 더 나은 쪽을 앵커에 쓴다.
- 간격 6초는 Q4 결정 전까지 그대로 둔다.

### 4단계: 사업자 반영 지원 흐름 (Q3 결정 필요)

- `analysis/types.py`의 `Profile`에 `business_registered: bool = False`, `tax_invoice: bool = False`를 추가하고 설정 화면(`desktop/src/features/settings/SettingsDialog.tsx`)과 계약(`scripts/gen-contracts.sh`)을 갱신한다.
- `jobs/auto_apply.py:78`: 프로필이 사업자면 사업자·세금계산서 요구 공고를 충돌로 보지 않고, 분석 결과에 "사업자·세금계산서 대응 가능" 긍정 사유를 붙인다. 사업자가 아니면 지금처럼 충돌로 둔다.
- `user_profile.skills`를 채운다(초안 문구와 점수에 쓰임).
- 당근 계정의 **기본 자기소개**는 사용자가 당근에서 직접 바꾼다(앱은 계정 설정을 바꾸지 않음). 바꾸면 자동 지원이 `profile_changed`로 멈추므로 앱에서 연결을 다시 승인한다. 문안 초안:

  > 안녕하세요, 개인사업자로 등록된 프리랜서 개발자입니다. 세금계산서·현금영수증 발행이 가능합니다.
  > 홈페이지·랜딩페이지·쇼핑몰 제작과 수정, 업무 자동화(엑셀·VBA·파이썬), 간단한 웹앱을 재택·비대면으로 진행합니다.
  > 진행: 요구사항 확인 → 견적·일정 안내 → 중간 결과 공유 → 납품 → 수정 반영.
  > 포트폴리오: (링크) / 연락 가능 시간: (시간)

### 5단계: 자동 지원 화면 재검증 (Q5 결정 필요)

- 로그인된 전용 Edge(`jobs-auto-browser-profile`)에서 실제 공고의 지원 화면을 **최종 지원 버튼을 누르지 않고** 열어 DOM을 확인한다: 안내 문구 "제출할 자기소개를 확인하세요", "기본" 탭, `지원하기` 버튼(`auto-content.js:33,37,68-69`).
- 바뀐 부분을 고치고 `extensions/tests/auto-ui.test.mjs`·`auto-worker.test.mjs`를 갱신한다.
- 자동 지원 조건(`auto_apply.py:51-80`)의 `pay_unit == "project"` 제한은 2단계 결정에 맞춰 조정한다.

### 6단계: 아이보스 (Q2 결정 필요)

- 권장 A: 개발 분류만 남기고 알림 전용으로 둔다. `sources/iboss/adapter.py`의 `_budget`에서 "월간"을 월급(`month`)으로 보지 않게 바꿔 "월급 지급" 오분류를 없앤다. 분류 필터 파라미터는 앱 Fetcher로 확인한다(일반 curl은 403).
- B를 고르면 위시켓·크몽 등 후보 사이트의 약관·robots·공개 범위 조사를 별도 프로젝트로 시작한다(예전 `D:\freelance-os-data\browser-sessions\wishket`, `kmong` 흔적 있음).

### 7단계: 운영

- `feat/apply-fast-path`의 텔레그램 알림(`4b65387`)을 현재 브랜치로 가져와 구조 변경·신규 추천 알림을 휴대폰으로 받는다.
- DB 크기 정리(보존 기간, `VACUUM`)는 백업 후 진행한다.

---

## Self-Review

- **스펙 대비:** P1→Task 1·2, P2→Task 4, P3→Task 3, P11→Task 0, 설치 확인→Task 5. P4~P10·P12는 2~7단계로 배정했다.
- **자리표시자:** 1단계 작업에는 TBD 없음. 4단계 문안의 "(링크)", "(시간)"은 사용자 입력 칸(Q3)이다.
- **이름 일관성:** `relay_records`, `SerializedError`, `_Reader`, `js`, `tsr_script`, `as_tsr`, `_salary_type`, `_alert_structure_change`, `STRUCTURE_ALERT_MIN_PARSE_FAILURES`가 정의된 작업과 사용하는 작업에서 같은 이름이다.
- **실측 근거:** 디코더와 어댑터 연결은 2026-11-07 실제 상세·피드 페이지로 스크래치 시험을 통과했다(상세 정상 해석, 피드 20건, 라우터 식 전체 해석). Task 1의 코드 블록을 이 문서에서 그대로 추출해 실행한 결과 10개 테스트 통과, 실제 페이지 두 개 모두 `client:root` 확인.
