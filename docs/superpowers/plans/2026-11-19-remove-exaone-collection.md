# EXAONE 제거 · 수집 개선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EXAONE 보조 기능을 모두 걷어 내 자동 지원을 저장된 기본 소개 제출로 되돌리고, 의뢰 추정 규칙·제목 동의어·빠른 확인 슬롯으로 수집을 보강한다.

**Architecture:** EXAONE 전용으로 바뀐 파일은 EXAONE 작업 직전 커밋 `648e4a9`의 내용으로 되돌린다. EXAONE과 다른 변경(포트폴리오 주소, 절전 막기 화면, 시트 수집어, `grounding.py`, `assist_eligible`)이 섞인 파일만 손으로 고친다. 데이터 정리는 마이그레이션 0011이 맡는다. 수집 보강은 기존 규칙 모듈(`rules.py`, `service.py`), 당근 어댑터 설정(`profile.json`, `_TITLE_ALIASES`), 우선 앵커 선정(`freshness.py`)을 작게 고친다.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy · Alembic(SQLite) · pytest / React · TypeScript · Vitest / MV3 확장 · node:test

**Spec:** `docs/superpowers/specs/2026-11-19-remove-exaone-collection-design.md`

## Global Constraints

- 저장소: `C:\Users\Administrator\.gemini\antigravity\scratch\worklead`, 브랜치 `feat/daangn-region-feed`.
- EXAONE 직전 기준 커밋: `648e4a9`. 되돌리기는 `git checkout 648e4a9 -- <path>`로 한다.
- 남의 미커밋 변경 `backend/worklead/engine/runs.py`, `backend/worklead/engine/executors.py`, `backend/tests/test_collector_lanes.py`는 수정하지 않고 커밋에도 넣지 않는다. `git add`는 늘 경로를 지정한다(`git add -A`, `git commit -a` 금지). 이 변경은 11-15 설치본에 이미 들어 있으므로 빌드는 작업 트리 그대로 한다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 새 마이그레이션 revision id: `0011_remove_ai_assist`(down_revision `0010_ai_assist`).
- 자동 지원 사유 코드 `ai_text_unavailable`은 응답 모델 Literal(`jobs/auto_apply_models.py`)과 화면 라벨에 남긴다. 라벨 문구: `지원 문구를 만들지 못함`.
- 검토 사유 문구(정확히): `의뢰 표현 추정 — 원문 확인`.
- 당근 일일 예산 5,500은 바꾸지 않는다. `daily_list_requests(5)`는 4,864 이하여야 한다.
- 백엔드 테스트: `cd backend && uv run pytest -q`. 화면: `cd desktop && npm run typecheck && npm test`. 확장: `cd extensions && npm test`.
- 실제 사용자 DB(`%LOCALAPPDATA%\Worklead\worklead.sqlite3`)에는 쓰지 않는다. 읽을 때는 사본을 만들어 읽는다. 설치(Task 8)만 예외다.
- 확인 중에 당근 "지원하기"를 최종 클릭하지 않는다.

## Review Focus

1. 실제 DB의 `ai` 설정이 `provider: "exaone"`, `enabled: true`, `assist_*` 키를 가진 채 0011로 올라간다. 설정 화면 `GET/PATCH /v1/settings`가 422나 500 없이 동작해야 한다. → Task 3 테스트 `test_live_like_ai_row_is_clean_after_upgrade`.
2. 프로필 데이터에 `price_anchor_note`가 저장돼 있어도 `GET /v1/settings`가 동작해야 한다. → Task 3의 같은 테스트.
3. 과거 시도 기록에 `reason_code="ai_text_unavailable"` 행이 있어도 자동 지원 상태 API가 동작해야 한다. → Task 1 테스트 `test_legacy_ai_text_unavailable_row_still_lists`.
4. EXAONE이 '검토 필요'로 올려 둔 리드(구매 의도 `unknown`)와 규칙이 추정한 리드(근거 `inferred`)에는 자동 지원이 나가지 않아야 한다. → Task 1 테스트 `test_inferred_or_unknown_intent_is_never_auto_applied`.
5. 0010 백업을 복원하면 0011로 올라가야 한다. → Task 3에서 고치는 기존 백업 테스트 4개.

---

### Task 1: 자동 지원을 기본 소개 제출로 되돌리기

**Files:**
- Restore from `648e4a9`: `backend/worklead/jobs/auto_apply.py`, `backend/worklead/jobs/managed_auto.py`, `backend/worklead/jobs/managed_browser.py`, `backend/worklead/jobs/chrome_bridge.py`, `extensions/worklead-messages/auto-content.js`, `extensions/worklead-messages/auto-protocol.js`, `extensions/worklead-messages/auto-worker.js`, `extensions/tests/auto-ui.test.mjs`, `extensions/tests/auto-worker.test.mjs`, `backend/tests/test_job_auto_apply.py`
- Delete: `backend/tests/test_auto_apply_ai_path.py`, `backend/tests/test_managed_auto_tailored.py`
- Modify: `backend/worklead/jobs/auto_apply.py` (`condition`)
- Test: `backend/tests/test_job_auto_apply.py`

**Interfaces:**
- Produces: `condition(s, lead, now, min_score, *, queue=False, visible_form=False) -> str | None`는 시그니처가 그대로다. 근거가 `explicit`/`user_confirmed`가 아닌 구매 의도면 `"condition_changed"`를 낸다. 자동 지원 작업 JSON은 5키(`attempt_id`, `lead_id`, `title`, `url`, `lease_token`)만 쓴다.

- [ ] **Step 1: 파일 되돌리기와 삭제**

```bash
git checkout 648e4a9 -- backend/worklead/jobs/auto_apply.py backend/worklead/jobs/managed_auto.py backend/worklead/jobs/managed_browser.py backend/worklead/jobs/chrome_bridge.py extensions/worklead-messages/auto-content.js extensions/worklead-messages/auto-protocol.js extensions/worklead-messages/auto-worker.js extensions/tests/auto-ui.test.mjs extensions/tests/auto-worker.test.mjs backend/tests/test_job_auto_apply.py
git rm -q backend/tests/test_auto_apply_ai_path.py backend/tests/test_managed_auto_tailored.py
```

`auto-content.js`의 링크형 버튼 인식(`a[href]`, 0c336ef)은 648e4a9에 이미 있다. 되돌린 뒤 `buttons()`에 `a[href]`가 남아 있는지 확인한다.

- [ ] **Step 2: 실패하는 테스트 쓰기** (`backend/tests/test_job_auto_apply.py`, 기존 `add`, `app` 도우미 사용)

```python
@pytest.mark.parametrize("intent,basis", [("buyer_project", "inferred"), ("unknown", None)])
def test_inferred_or_unknown_intent_is_never_auto_applied(app, intent, basis):
    lead_id = add(app, 80)
    with app.db.session() as s:
        lead = s.get(Lead, lead_id)
        lead.demand_intent, lead.demand_intent_basis = intent, basis
        assert condition(s, lead, NOW, 50, queue=True) == "condition_changed"

def test_legacy_ai_text_unavailable_row_still_lists(app):
    lead_id = add(app)
    with app.db.session() as s:
        s.add(JobAutoAttempt(id=new_id("auto"), job_key="k" * 64, lead_id=lead_id, state="held",
                             reason_code="ai_text_unavailable", created_at=NOW, finished_at=NOW, retry_count=0))
    bridge, client, _ = connect(app)
    assert bridge.status().model_dump()  # does not raise on the legacy reason code
```

상태 조회 함수 이름이 `status()`가 아니면 되돌린 `auto_apply.py`에서 공개 상태 메서드를 찾아 쓴다. 이 테스트가 확인할 것은 레거시 사유 코드 행이 있어도 응답 모델이 실패하지 않는다는 점이다.

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `cd backend && uv run pytest tests/test_job_auto_apply.py -q -k "inferred_or_unknown or legacy_ai_text"`
Expected: `buyer_project-inferred` 경우가 FAIL(None 반환). `unknown`은 기존 규칙으로 이미 PASS.

- [ ] **Step 4: `condition`에 근거 조건 추가**

처음 `if` 묶음의 구매 의도 검사(`lead.demand_intent not in ("buyer_project", "buyer_ongoing")`) 옆에 `or lead.demand_intent_basis not in ("explicit", "user_confirmed")`를 더한다. 한 줄 주석: 규칙이 추정한 의뢰는 사람이 확인한다.

- [ ] **Step 5: 백엔드 자동 지원 테스트와 확장 테스트 통과 확인**

Run: `cd backend && uv run pytest tests/test_job_auto_apply.py tests/test_auto_apply_retry.py tests/test_auto_apply_preparation.py tests/test_managed_auto.py tests/test_auto_apply_browser.py tests/test_auto_apply_recovery.py -q`
Expected: PASS
Run: `cd extensions && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/worklead/jobs backend/tests/test_job_auto_apply.py extensions/worklead-messages extensions/tests
git commit -m "revert(auto-apply): submit the saved default intro again; inferred intent never auto-applies"
```

---

### Task 2: EXAONE 백엔드 코드 제거

**Files:**
- Delete: `backend/worklead/analysis/exaone_provider.py`, `assist.py`, `writer.py`, `recheck.py`, `chat_draft.py`, `prompts.py`; `backend/tests/exaone_fixtures.py`, `test_exaone_provider.py`, `test_exaone_review.py`, `test_ai_writer.py`, `test_ai_recheck.py`, `test_assist_core.py`, `test_chat_draft.py`, `test_quote_draft.py`, `test_ai_provider_choice.py`
- Restore from `648e4a9`: `backend/worklead/api/app.py`, `backend/worklead/api/job_message_routes.py`, `backend/worklead/jobs/chat_view.py`, `backend/worklead/context.py`, `backend/worklead/server.py`, `backend/worklead/services/credentials.py`
- Modify: `backend/worklead/analysis/coordinator.py`, `backend/worklead/analysis/types.py`, `backend/worklead/engine/pipeline.py`, `backend/worklead/api/schemas.py`, `backend/worklead/services/settings.py`
- Create test: `backend/tests/test_portfolio_profile.py`
- Contracts: `contracts/**` 재생성

**Interfaces:**
- Consumes: Task 1(`auto_apply.py`가 더 이상 `writer`/`assist`/`recheck`를 import하지 않음).
- Produces
  - `Profile`에 `price_anchor_note`가 없다. `portfolio_url: str = "https://www.seyj.kr/"`는 남는다.
  - `NON_SCORING_PROFILE_KEYS == frozenset({"portfolio_url"})`.
  - `pipeline.cache_profile(profile)`는 그대로 남는다.
  - `schemas.AISettings.provider`는 `Literal["openai"]`. `AISettings`·`AISettingsPatch`·`AIStatus`에 `daily_call_cap`, `assist_*`, `calls_today`가 없다.
  - `QuoteDraftOut`가 없다.
  - `settings.DEFAULTS`에 `exaone_calls`가 없다.

- [ ] **Step 1: 삭제와 되돌리기**

```bash
git rm -q backend/worklead/analysis/exaone_provider.py backend/worklead/analysis/assist.py backend/worklead/analysis/writer.py backend/worklead/analysis/recheck.py backend/worklead/analysis/chat_draft.py backend/worklead/analysis/prompts.py backend/tests/exaone_fixtures.py backend/tests/test_exaone_provider.py backend/tests/test_exaone_review.py backend/tests/test_ai_writer.py backend/tests/test_ai_recheck.py backend/tests/test_assist_core.py backend/tests/test_chat_draft.py backend/tests/test_quote_draft.py backend/tests/test_ai_provider_choice.py
git checkout 648e4a9 -- backend/worklead/api/app.py backend/worklead/api/job_message_routes.py backend/worklead/jobs/chat_view.py backend/worklead/context.py backend/worklead/server.py backend/worklead/services/credentials.py
```

- [ ] **Step 2: 남길 테스트를 새 파일로 쓰기** (`backend/tests/test_portfolio_profile.py`, 기존 `ctx`, `client` 픽스처)
  - `test_portfolio_must_be_https`: 삭제한 `test_ai_provider_choice.py`의 같은 테스트를 그대로 옮긴다.
  - `test_portfolio_change_does_not_bump_version`: `update_profile(s, {"portfolio_url": "https://example.com/"}) is False`이고 버전이 같다. `update_profile(s, {"skills": "React"}) is True`이면 버전이 +1이다.
  - `test_paid_cache_profile_ignores_portfolio`: `cache_profile`의 키 집합이 `{"services","skills","excluded_work","min_contract","target_hourly","weekly_hours","onsite","allow_short_term_employment","business_registered","tax_invoice","version"}`이다. `portfolio_url`을 바꿔도 값이 같다.
  - `test_settings_have_no_assist_fields`: `client.get("/v1/settings").json()`의 `ai`에 `daily_call_cap`, `assist_review`가 없고, `profile`에 `price_anchor_note`가 없고, `profile.portfolio_url == "https://www.seyj.kr/"`이다.

- [ ] **Step 3: 손으로 고치기**
  - `coordinator.py`
    - `_analyze_exaone`과 `if ticket.config.get("provider") == "exaone"` 분기를 지운다.
    - `dataclasses`, `math`, `time`, `grounding` import는 더 쓰이지 않으면 지운다.
    - `row.engine != f"ai:{cfg.get('provider', 'openai')}"`를 `row.engine != "ai:openai"`로 바꾼다.
    - `assist_eligible`은 그대로 둔다.
  - `types.py`: `price_anchor_note` 필드를 지우고 `NON_SCORING_PROFILE_KEYS = frozenset({"portfolio_url"})`로 둔다.
  - `pipeline.py`
    - `apply_analysis`에서 `exaone` 변수와 `assist_review` 조건을 지운다.
    - `engine`/`model`을 648e4a9 식(`f"ai:{provider.engine}"`, `provider.model`)으로 되돌리되 `ai.assist_eligible(ra)` 조건은 남긴다.
    - `keep_promotion` 두 줄을 지운다.
    - `generate_draft`의 `writer` 분기를 지운다.
  - `schemas.py`
    - `AISettings`·`AISettingsPatch`·`AIStatus`의 provider/model Literal을 openai 하나로 돌린다.
    - `daily_call_cap`·`assist_*`·`calls_today`, 그리고 그 필드들을 가리키는 validator 이름을 지운다.
    - `QuoteDraftOut`과 `ProfileSettings.price_anchor_note`를 지운다. `portfolio_url`은 남긴다.
  - `settings.py`
    - `DEFAULTS["ai"]`의 `daily_call_cap`과 `assist_*`, `DEFAULTS["exaone_calls"]`를 지운다.
    - `DEFAULT_PROFILE`과 `get_profile`의 `price_anchor_note`를 지운다.
    - 시트 수집어, `portfolio_url`, `NON_SCORING_PROFILE_KEYS` 처리, `stop_pending_ai`의 `like("ai:%")`는 남긴다.
  - 되돌린 `app.py`의 `settings_out`이 `ProfileSettings(**data)`에 모르는 키(`price_anchor_note`)를 넘기지 않게 한다. `ProfileSettings.model_fields`에 있는 키만 넘긴다. 0011 이전 DB나 복원 직후를 위한 방어다.

- [ ] **Step 4: 남은 참조가 없는지 확인**

Run: `cd backend && grep -rn "exaone\|assist_worker\|chat_drafts\|writer\.\|recheck\|price_anchor\|calls_today\|QuoteDraft" worklead tests --include=*.py`
Expected: `migrations/versions/0010_ai_assist.py`와 아직 손대지 않은 `models.py`(`AiDraft`, `ai_recheck`) 말고는 일치가 없다.

- [ ] **Step 5: 계약 재생성과 백엔드 전체 테스트**

Run: `bash scripts/gen-contracts.sh`
Expected: 마지막 줄 `contracts regenerated`. `contracts/` 변경에 `exaone`, `assist_`, `quote-draft`가 남지 않는다(`grep -rn "exaone\|assist_\|quote-draft" contracts` 결과 0).
Run: `cd backend && uv run pytest -q`
Expected: PASS. head revision을 단정하는 기존 테스트 4개(Task 3 목록)는 아직 `0010_ai_assist` 그대로라 통과한다.

- [ ] **Step 6: Commit**

```bash
git add backend/worklead backend/tests contracts
git commit -m "refactor(ai): remove EXAONE assist, chat and quote drafts; keep OpenAI analysis and portfolio field"
```

---

### Task 3: 마이그레이션 0011과 모델 정리

**Files:**
- Create: `backend/worklead/migrations/versions/0011_remove_ai_assist.py`
- Restore from `648e4a9`: `backend/worklead/models.py`, `backend/worklead/storage.py`
- Rename + rewrite: `backend/tests/test_migration_0010.py` → `backend/tests/test_migration_0011.py`
- Modify: `backend/tests/test_auto_apply_retry.py:154`, `backend/tests/test_cafe_m1.py:113`, `backend/tests/test_iboss_integration.py:89`, `backend/tests/test_relations_quality.py:280` (`"0010_ai_assist"` → `"0011_remove_ai_assist"`)

**Interfaces:**
- Consumes: Task 2(코드에서 `AiDraft`, `Lead.ai_recheck` 사용이 없음).
- Produces: head revision `0011_remove_ai_assist`. `models.py`에 `AiDraft`와 `Lead.ai_recheck`가 없다.

- [ ] **Step 1: 실패하는 테스트 쓰기** (`backend/tests/test_migration_0011.py`)
  - `test_head_revision_drops_ai_tables(ctx)`
    - `storage.current_revision(...) == "0011_remove_ai_assist"`
    - `ai_drafts` 테이블이 없다.
    - `leads`에 `ai_recheck` 열이 없다.
  - `test_live_like_ai_row_is_clean_after_upgrade(ctx, client)` (Review Focus 1, 2)
    - `command.downgrade(..., "0010_ai_assist")`를 한 뒤 sqlite3로 다음 행을 넣는다.
      - `app_settings` `ai` = `{"enabled": true, "external_transfer_consent": true, "provider": "exaone", "model": "k-exaone_v2.5", "daily_call_cap": 300, "assist_review": true, "assist_recheck": true, "assist_application": true, "assist_chat": true, "assist_quote": true, "max_output_tokens": 1024, "pricing_confirmed": false, "price_version": null, "monthly_cost_cap": null, "daily_cost_cap": null, "usd_to_krw_rate": null, "engine_label": null}`
      - `ai_revision` = `4`
      - `exaone_calls` = `{"day": "2026-11-19", "count": 41}`
      - `user_profile` id 1의 data에 `"price_anchor_note": "x"`
      - `ai_drafts` 행 1개
    - 다시 `command.upgrade(..., "head")`를 한다.
    - 단정
      - `ai` 행: `provider == "openai"`, `model == "gpt-4.1-mini-2025-04-14"`, `enabled is False`, `assist_*`·`daily_call_cap` 키 없음
      - `ai_revision == 5`
      - `exaone_calls` 행 없음
      - 프로필 data에 `price_anchor_note` 없음
      - `client.get("/v1/settings").status_code == 200`
      - 그 응답의 `ai.revision`·`database_generation`으로 `{"ai": {"enabled": False, ...}}`를 PATCH하면 200
  - `test_downgrade_then_upgrade_round_trip(ctx)`: `0010_ai_assist`로 내렸다 head로 올리면 `validate_database(...)[0] == "0011_remove_ai_assist"`다.
  - alembic 명령 전에는 기존 0010 테스트처럼 `ctx.db.dispose()`를 부른다.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_migration_0011.py -q`
Expected: FAIL. `0011_remove_ai_assist` revision이 없다.

- [ ] **Step 3: 마이그레이션 구현** (`0011_remove_ai_assist.py`)
  - `upgrade()`
    - `op.drop_index("ix_ai_draft_due", table_name="ai_drafts")`, `op.drop_table("ai_drafts")`
    - `with op.batch_alter_table("leads") as b: b.drop_column("ai_recheck")`
    - `op.get_bind()`로 설정 행을 고친다.
      - `ai` JSON: 제공자가 `exaone`이면 `provider="openai"`, `model="gpt-4.1-mini-2025-04-14"`, `enabled=False`로 바꾼다. `daily_call_cap`과 `assist_review/recheck/application/chat/quote` 키를 뺀다.
      - `ai_revision`이 있으면 정수 +1. 없으면 손대지 않는다.
      - `exaone_calls` 행을 지운다.
      - `user_profile` data의 `price_anchor_note` 키를 지운다.
    - `app_settings`의 값 열은 JSON 문자열로 읽고 쓴다(0010 이전 마이그레이션의 설정 갱신 방식을 따른다).
  - `downgrade()`: 0010의 `upgrade()`와 같은 구조(`ai_recheck` 열, `ai_drafts` 테이블, 인덱스)를 다시 만든다. 설정 데이터는 되살리지 않는다.

- [ ] **Step 4: 모델·저장소 되돌리기와 head 단정 갱신**

```bash
git checkout 648e4a9 -- backend/worklead/models.py backend/worklead/storage.py
git mv backend/tests/test_migration_0010.py backend/tests/test_migration_0011.py
```

(새 테스트 내용은 Step 1에서 쓴 것으로 덮어쓴다.) 기존 테스트 4곳의 `"0010_ai_assist"`를 `"0011_remove_ai_assist"`로 바꾼다. 되돌린 `storage.validate_database`는 남는 테이블·열만 검사하므로, 0010 백업에 남은 `ai_drafts`·`ai_recheck`는 문제가 되지 않는다.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_migration_0011.py tests/test_auto_apply_retry.py tests/test_cafe_m1.py tests/test_iboss_integration.py tests/test_relations_quality.py tests/test_backup_restore_quality.py -q`
Expected: PASS
Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/worklead/migrations/versions/0011_remove_ai_assist.py backend/worklead/models.py backend/worklead/storage.py backend/tests/test_migration_0011.py backend/tests/test_auto_apply_retry.py backend/tests/test_cafe_m1.py backend/tests/test_iboss_integration.py backend/tests/test_relations_quality.py
git commit -m "feat(db): 0011 drops ai_drafts and leads.ai_recheck, resets EXAONE settings to disabled OpenAI"
```

---

### Task 4: 데스크톱 화면에서 EXAONE 제거

**Files:**
- Restore from `648e4a9`: `desktop/src/features/settings/AiSettingsPanel.tsx`, `desktop/src/features/jobs/MessageInboxPanel.tsx`, `desktop/src/features/detail/LeadDetailPane.tsx`, `desktop/src/__tests__/ai-settings.test.tsx`, `desktop/src/__tests__/message-inbox.test.tsx`
- Delete: `desktop/src/features/detail/QuoteDraftPanel.tsx`, `desktop/src/__tests__/quote-draft.test.tsx`
- Modify: `desktop/src/domain/model.ts`, `desktop/src/data/port.ts`, `desktop/src/data/live/mapper.ts`, `desktop/src/data/live/livePort.ts`, `desktop/src/data/mock/mockPort.ts`, `desktop/src/features/settings/SettingsDialog.tsx`, `desktop/src/features/jobs/AutoApplyPanel.tsx`, `desktop/src/styles/app.css`

**Interfaces:**
- Consumes: Task 2에서 다시 생성한 `contracts/client`(EXAONE 필드 없음).
- Produces: `SettingsView.ai.provider: 'openai'`, `SettingsView.profile.portfolioUrl`, `SettingsView.collection.preventSleep`가 남는다. `WorkleadPort`에 `requestQuoteDraft`/`quoteDraft`가 없다.

- [ ] **Step 1: 되돌리기와 삭제**

```bash
git checkout 648e4a9 -- desktop/src/features/settings/AiSettingsPanel.tsx desktop/src/features/jobs/MessageInboxPanel.tsx desktop/src/features/detail/LeadDetailPane.tsx desktop/src/__tests__/ai-settings.test.tsx desktop/src/__tests__/message-inbox.test.tsx
git rm -q desktop/src/features/detail/QuoteDraftPanel.tsx desktop/src/__tests__/quote-draft.test.tsx
```

- [ ] **Step 2: 손으로 고치기**
  - `model.ts`: `priceAnchorNote`, `dailyCallCap`, `assist*`, `callsToday`를 지운다. provider/model 유니온을 openai 하나로 돌린다. `portfolioUrl`, `collection`은 남긴다.
  - `port.ts`: `QuoteDraft` 타입과 두 메서드, ai 패치 키 목록의 `dailyCallCap`·`assist*`를 지운다. `collection?`은 남긴다.
  - `mapper.ts`, `livePort.ts`, `mockPort.ts`: 같은 필드와 견적 메서드를 지운다. `portfolio_url`·`prevent_sleep` 매핑은 남긴다.
  - `SettingsDialog.tsx`: "기준가 안내" 칸과 그 설명을 지운다. 포트폴리오 칸 설명은 `당근 기본 자기소개에 넣을 포트폴리오 주소를 보관합니다.`로 바꾼다. 절전 방지 토글은 남긴다.
  - `AutoApplyPanel.tsx`: `ai_text_unavailable` 라벨을 `지원 문구를 만들지 못함`으로 바꾼다.
  - `app.css`: `.quote-draft-text` 규칙을 지운다.

- [ ] **Step 3: 테스트에 단정 하나 추가** (`desktop/src/__tests__/ai-settings.test.tsx`)

AI 설정 화면을 렌더하면 `EXAONE`이라는 글자가 없고, `제공자를 저장한 뒤 키를 입력하세요`라는 문구도 없다. 키 입력 칸은 활성 상태다.

- [ ] **Step 4: 확인**

Run: `cd desktop && npm run typecheck && npm test`
Expected: 타입 오류 0, 테스트 PASS.
Run: `grep -rn "exaone\|EXAONE\|assist\|quoteDraft\|priceAnchor\|callsToday" desktop/src`
Expected: 일치 0.

- [ ] **Step 5: Commit**

```bash
git add desktop/src
git commit -m "refactor(ui): remove EXAONE settings, assist switches, chat and quote drafts"
```

---

### Task 5: 의뢰 추정 규칙

**Files:**
- Modify: `backend/worklead/analysis/rules.py`, `backend/worklead/analysis/service.py:325-330` (의도 판정 직후), `backend/worklead/analysis/service.py` 검토 사유 목록
- Create test: `backend/tests/test_inferred_request.py`

**Interfaces:**
- Produces
  - `rules.inferred_request(title: str, body: str) -> Ev | None`. 근거가 `"inferred"`인 `Ev`를 내거나, 조건이 맞지 않으면 `None`을 낸다.
  - `analyze()`가 근거 `inferred`인 `demand_intent=buyer_project` 판정을 낼 수 있다. Task 1의 `condition`이 이 판정을 막는다.

- [ ] **Step 1: 실패하는 테스트 쓰기** (`tests.test_software_request_coverage.check(title, body, pay=...)` 사용)

```python
POSITIVE = [
    ("AI 활용 웹사이트 제작", "자꾸 AI로 15분 30분에 해준신다고 하는데 4시간에 제대로 빌딩 완료하실수 있는 분이면 충분합니다. 소요시간 작업일자등은 협의가능합니다. 제작배포까지 책임감있게 약속지키시면 계속 드립니다"),
    ("코딩할 수 있는 분 구합니다", "HTML과 CSS를 다룰 줄 아시는 분을 찾고 있어요. 데이터 정리 자동화 작업과 함께 코드 수정 및 오류 해결을 도와주시면 됩니다."),
    ("원격결재 시스템 구축", "1.원격 결재시스템 구축. 2.전기 자전거 홍보 브러그 구축 1.2번을 완성하고.브러그 활동을 도와주실 분."),
    ("상세페이지 제작 가능한분 구함(이미지 다운가능해야함)", "상세페이지 제작 가능한 분 구합니다. 상세페이지에 들어갈 이미지는 합법적인 사이트에서 다운로드 가능한 분이었으면 좋겠습니다.. 건티슈 판매 관련 상세페이지입니다"),
    ("웹디자이너) 재택 / 홈페이지 디자인 작업 가능하신분", "홈페이지 디자인재택으로 가능하신 분 한분 모집합니다. UX/UI 개발은 저희가 진행합니다. 많은 지원 바랍니다."),
]
NEGATIVE = [
    ("바이브코딩 알려주실분", "바이브 코딩 알려주실분 구합니다 ** 문자바랍니다"),
    ("코딩 과학 파트강사 구합니다", "초등 코딩 과학 수업을 맡아 주실 강사님을 모집합니다."),
    ("자동화설비 제작 및 셋업 팀원 충원", "자동화 설비 제작 및 셋업을 함께할 분 모집합니다."),
    ("쇼핑몰 포장 알바 구해요", "쇼핑몰 상품 포장 도와주실 분 구합니다."),
]

@pytest.mark.parametrize("title,body", POSITIVE)
def test_unworded_software_request_goes_to_review_with_inferred_basis(title, body):
    result = check(title, body)
    intent = result.judgements["demand_intent"]
    assert (intent.value, intent.basis) == ("buyer_project", "inferred")
    assert result.recommendation == "needs_review"
    assert any(r.text == "의뢰 표현 추정 — 원문 확인" for r in result.reasons)

@pytest.mark.parametrize("title,body", NEGATIVE)
def test_teaching_physical_and_packing_posts_are_not_inferred(title, body):
    assert check(title, body).judgements["demand_intent"].basis != "inferred"

def test_explicit_buyer_keeps_explicit_basis():
    intent = check("홈페이지 리뉴얼 도와주실분", "홈페이지 리뉴얼을 도와주실 분을 찾고 있어요.").judgements["demand_intent"]
    assert intent.basis == "explicit"
```

`check()`가 기본으로 붙이는 금액(건당 30만원) 때문에 POSITIVE 예시에서 시급 사유가 섞이지 않는다. 시급 예시는 `test_remote_time_pay_review.py`의 기존 규칙이 맡는다.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_inferred_request.py -q`
Expected: POSITIVE 5건 FAIL(`intent.value == "unknown"`).

- [ ] **Step 3: `rules.inferred_request` 구현**

`rules.py`에 패턴 세 묶음을 `c(...)`로 둔다.
- 결과물과 만들기가 가까이 있는 표현: 결과물 `홈\s*페이지|홈피|웹\s*사이트|사이트|랜딩\s*페이지|상세\s*페이지|쇼핑몰|앱|어플|프로그램|시스템|코드|엑셀|구글\s*시트|자동화|매크로|크롤링|API|챗봇`과 만들기 `제작|개발|구축|수정|코딩|만들|연동|오류|디자인`이 마침표·줄바꿈 없이 15자 안에 있다(양방향).
- 맡기는 표현: `분\s*(?:을\s*)?(?:구합니다|구해요|구함|찾습니다|찾아요|찾고|모집|모십니다)`, `도와\s*주(?:실|시면|세요)`, `도움\s*주(?:실|시면)`, `해\s*주(?:실|시면)`, `가능(?:하신|한)\s*분`, `(?:하실|할)\s*수\s*있는\s*분`, `하실\s*분`.
- 막는 표현(제목): `설비|장비|생산|조립|공장|현장|시공|설치|포장|강사|선생님|과외|레슨|수업|강의`.

제목+본문에서 앞의 두 묶음이 모두 맞고 제목에 막는 표현이 없으면, 맡기는 표현을 `find(..., "inferred", "결과물·제작 표현과 맡기는 표현 — 의뢰로 추정")`로 찾아 `Ev`를 돌려준다.

- [ ] **Step 4: `service.analyze` 연결**

`non_delivery` 처리 바로 뒤, `classify_engagement` 호출 전에 둔다. `intent.value == "unknown" and not non_delivery and dev`이고 `rules.inferred_request(title, body)`가 `Ev`를 내면 `intent = Judgement("demand_intent", "buyer_project", "inferred", [ev])`로 바꾼다. 검토 사유 목록에는 `intent.basis == "inferred"`일 때 `"의뢰 표현 추정 — 원문 확인"`을 앞쪽에 넣는다. 그래야 사유 상한(2개) 안에 남는다.

- [ ] **Step 5: 통과와 회귀 확인**

Run: `cd backend && uv run pytest tests/test_inferred_request.py tests/test_delivery_help_requests.py tests/test_software_request_coverage.py tests/test_remote_time_pay_review.py tests/test_analysis.py tests/test_analysis_quality.py -q`
Expected: PASS. 기존 테스트가 깨지면 규칙을 좁힌다(막는 표현 추가, 거리 축소). 기존 기대값은 바꾸지 않는다.

- [ ] **Step 6: 실데이터 재현 (읽기 전용 사본)**

스크래치 폴더에 스크립트를 쓴다. 순서:
1. `sqlite3.connect(live).backup(copy)`로 실제 DB를 사본으로 뜬다.
2. 사본에서 2026-11-10 06:00 UTC 이후 `daangn-alba` 기록마다 `analyze(post_input_from_record(rec, "site"), get_profile(s), now, ttl_hours(s), feedback_map(s, lead.id))`를 돌린다.
3. 기존 `leads.recommendation`이 `excluded`였는데 새 결과가 `needs_review`가 된 목록(제목, 금액, 사유)을 출력한다.

사람이 목록을 보고 거짓 양성(강의, 현장 일, 채용)이 섞였으면 Step 3의 막는 표현을 늘리고 Step 5부터 다시 한다. 마지막 목록은 커밋 메시지 본문에 건수만 적는다.

- [ ] **Step 7: Commit**

```bash
git add backend/worklead/analysis/rules.py backend/worklead/analysis/service.py backend/tests/test_inferred_request.py
git commit -m "feat(analysis): infer unworded software requests and send them to review"
```

---

### Task 6: 제목 동의어 보강

**Files:**
- Modify: `backend/worklead/sources/daangn/adapter.py:32-42` (`_TITLE_ALIASES`), `backend/worklead/sources/daangn/profile.json` (`title_pattern`)
- Test: `backend/tests/test_daangn_title_scope.py`

**Interfaces:**
- Produces: `saved_candidate_kind(title, query)`가 "프로그래밍"과 "html" 제목을 `"core"`로 본다(검색어에 `프로그램`·`홈페이지` 묶음이 있을 때).

- [ ] **Step 1: 실패하는 테스트 쓰기**

```python
def test_programming_and_html_titles_reach_detail_check():
    assert kind("컴퓨터 프로그래밍 가능하신분 견적") == "core"
    assert create().saved_candidate_kind("AI영상/ html 코드 제작가능하신분 찾습니다", json.dumps(["홈페이지"], ensure_ascii=False)) == "core"

def test_programming_teacher_titles_stay_out():
    for title in ("프로그래밍 강사 모집", "html 강의 해주실 선생님"):
        assert create().saved_candidate_kind(title, json.dumps(["프로그램", "홈페이지"], ensure_ascii=False)) is None, title
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_daangn_title_scope.py -q`
Expected: 첫 테스트 FAIL.

- [ ] **Step 3: 구현**

`_TITLE_ALIASES`의 프로그램 묶음에 `"프로그래밍"`, 홈페이지 묶음에 `"html"`을 더한다. `profile.json`의 `title_pattern` 끝에 `|프로그래밍|html`을 더한다. 강사 제외(`_TEACHING_TITLE`)는 이미 모든 핵심 후보에 적용되므로 따로 고치지 않는다.

- [ ] **Step 4: 통과와 말뭉치 회귀 확인**

Run: `cd backend && uv run pytest tests/test_daangn_title_scope.py tests/test_daangn_m4_corpus.py tests/test_discovery_candidates_quality.py tests/test_discovery_exploration_quality.py tests/test_title_audit_sampling.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/worklead/sources/daangn/adapter.py backend/worklead/sources/daangn/profile.json backend/tests/test_daangn_title_scope.py
git commit -m "fix(daangn): treat 프로그래밍 and html titles as program and website terms"
```

---

### Task 7: 포화 오판 수정과 빠른 확인 재배분

**Files:**
- Modify: `backend/worklead/engine/freshness.py` (포화 선정, 주석)
- Modify: `backend/worklead/sources/daangn/profile.json` (앵커 9개 등급, `adaptive.max_hot_anchors`)
- Test: `backend/tests/test_fast_discovery.py`

**Interfaces:**
- Produces: `freshness.SATURATION_NEW_ENTRIES = 15`. 포화는 최근 작업의 `new_listed_entries >= 15`일 때만이다. `max_hot_anchors == 10`.

- [ ] **Step 1: 테스트 쓰기와 기존 단정 갱신** (`test_fast_discovery.py`)
  - 새 `test_full_page_with_one_new_entry_is_not_saturated(tmp_path)`
    - `feed-28`의 최근 작업 checkpoint가 `{"feed_saturated": True, "new_listed_entries": 1}`이고 `updated_at = NOW - 10분`이면, `prioritize_feed_tasks` 결과에서 `feed-28`의 `min_revisit_minutes != 5`다.
    - 같은 조건에서 `new_listed_entries: 16`이면 `== 5`다.
  - `test_daily_reserve_covers_rotating_hot_set_and_stays_below_host_cap` 갱신
    - `daily_list_requests(5) == 4828`
    - `remaining_list_requests(86400, 5) == 4939`
    - `remaining_list_requests(3600, 5) >= 61 * 2 + 38 + 10 * 6`
    - 추가: `adapter.daily_list_requests(5) <= 4864`
  - `test_unique_new_production_outranks_duplicates_and_hot_capacity_is_shared` 갱신
    - 처음 선정이 `{f"feed-{i}" for i in range(102, 112)}`
    - 빠른 작업을 `range(10)`개 넣으면 다음 선정에서 10분 앵커가 없다.
    - 10분 뒤 다시 `== 10`

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_fast_discovery.py -q`
Expected: 새 테스트와 갱신한 단정이 FAIL.

- [ ] **Step 3: 구현**
  - `freshness.py`: 모듈 상수 `SATURATION_NEW_ENTRIES = 15`를 둔다. 포화 후보 조건 `cp.get("feed_saturated") and cp.get("new_listed_entries", 0)`을 `int(cp.get("new_listed_entries", 0) or 0) >= SATURATION_NEW_ENTRIES`로 바꾼다. 한 줄 주석: `feed_saturated`는 페이지가 꽉 찼다는 뜻일 뿐이고, 새 항목이 페이지의 3/4을 넘을 때만 넘침 위험이 있다. "eight-slot" 주석은 "shared slot allowance (`max_hot_anchors`)"로 고친다.
  - `profile.json`
    - 지역 ID 672, 837, 2236, 2565, 3630, 4602, 5227, 6452, 6652 앵커의 등급을 `"A"`에서 `"C"`로 바꾼다.
    - `adaptive.max_hot_anchors`를 `10`으로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_fast_discovery.py tests/test_title_audit_sampling.py tests/test_unattended_collection.py tests/test_regional_schedule.py tests/test_daangn_feed.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/worklead/engine/freshness.py backend/worklead/sources/daangn/profile.json backend/tests/test_fast_discovery.py
git commit -m "fix(collect): saturate only on real overflow, move idle anchors to C and widen the hot set to 10"
```

---

### Task 8: 전체 검증, 설치, 기록, 푸시

**Files:**
- Modify: `docs/STATUS.md` (맨 위에 2026-11-19 섹션)

- [ ] **Step 1: 전체 테스트**

Run: `cd backend && uv run pytest -q` → PASS
Run: `cd desktop && npm run typecheck && npm test` → PASS
Run: `cd extensions && npm test` → PASS

- [ ] **Step 2: 빌드**

Run: `powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1`
Expected: 마지막 줄 `완료: desktop\src-tauri\target\release\bundle\nsis\`. 산출물은 `backend\build\dist\worklead-backend.exe`와 `desktop\src-tauri\target\release\worklead.exe`다.

- [ ] **Step 3: 설치 (2026-11-10 방식)**
  1. 사용자에게 앱을 닫아 달라고 하고, `worklead.exe`와 `worklead-backend.exe` 프로세스가 없는지 확인한다.
  2. `%LOCALAPPDATA%\Worklead\backups\pre-exaone-removal-20261119\`에 실행 파일 2개와 DB 사본(sqlite backup API로 `worklead.sqlite3`와 WAL 반영본)을 둔다.
  3. 빌드한 실행 파일 2개로 교체한다.
  4. 앱을 실행한 뒤 다음을 확인한다.
     - DB revision `0011_remove_ai_assist`, `PRAGMA quick_check` = ok
     - 리드 수가 설치 전과 같다
     - `ai` 설정: `provider=openai`, `enabled=false`
     - 자동 지원 설정(`job_auto_apply`, `job_auto_managed`)이 그대로다

- [ ] **Step 4: 기록**

`docs/STATUS.md` 맨 위에 2026-11-19 섹션을 쓴다. 기존 섹션 형식(범위, 수집, 제거, 확인함, 설치, 남은 위험, 사용자 조치)을 따른다.
- 사용자 조치
  - 기본 자기소개 교체 후 자동 지원 재확인
  - 사업자·세금계산서 체크
  - 절전 막기
  - 카페 재로그인
  - EXAONE 키 폐기
- 남은 위험
  - 3일 뒤 발견 지연과 일일 사용량을 다시 재야 한다
  - 남의 미커밋 변경(`runs.py`·`executors.py`)이 빌드에 들어갔다

```bash
git add docs/STATUS.md
git commit -m "docs: record EXAONE removal, collection fixes and install"
```

- [ ] **Step 5: 작성자 이메일을 noreply로 바꿔 푸시** (사용자가 요청함)

```bash
git config user.email "116673007+terry4025@users.noreply.github.com"
BASE=$(git rev-list HEAD --not --remotes | tail -1)^
git stash push -m foreign-wip -- backend/worklead/engine/runs.py backend/worklead/engine/executors.py backend/tests/test_collector_lanes.py
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --env-filter '
  if [ "$GIT_AUTHOR_EMAIL" = "terry40257te@gmail.com" ]; then export GIT_AUTHOR_EMAIL="116673007+terry4025@users.noreply.github.com"; fi
  if [ "$GIT_COMMITTER_EMAIL" = "terry40257te@gmail.com" ]; then export GIT_COMMITTER_EMAIL="116673007+terry4025@users.noreply.github.com"; fi
' -- $BASE..HEAD
git stash pop
git log --format='%ae %ce' $BASE..HEAD | sort | uniq -c   # gmail 주소가 0건이어야 한다
git push -u origin feat/daangn-region-feed
```

Expected: 푸시 성공. 이메일 개인정보 보호 거부가 없다. `git stash list`가 비어 있고, 남의 변경 3개 파일이 작업 트리에 그대로 있다.

## 다음 단계 (이 계획 밖)

- UI/UX 전면 개선: 이 계획을 설치한 뒤, `impeccable`(주도)과 `design-taste-frontend`(보조)로 따로 설계하고 계획한다.
- 3일 뒤 발견 지연·사용량 재측정. 효과가 작으면 당근 일일 예산 상향을 사용자와 정한다.
