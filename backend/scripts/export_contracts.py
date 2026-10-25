"""contracts/ 생성: openapi.json, schemas/, fixtures/ (실제 API 응답을 데모·합성 사이트로 생성).

사용: uv run python scripts/export_contracts.py
fixtures 는 response_model 직렬화를 거친 실제 응답이므로 스키마와 일치한다 (tests/test_contracts.py 가 재검증).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from tests import fixture_site as fx  # noqa: E402
from tests.conftest import client_for, make_ctx  # noqa: E402
from worklead.api import schemas as S  # noqa: E402
from worklead.engine import runs  # noqa: E402
from worklead.engine.events import emit  # noqa: E402
from worklead.engine.worker import Worker  # noqa: E402
from worklead.models import SourcePolicy  # noqa: E402
from worklead.services.demo import seed  # noqa: E402
from worklead.services.settings import put_setting  # noqa: E402
from worklead.sources.manual import ManualAdapter  # noqa: E402

OUT = ROOT / "contracts"
NATIVE_SCHEMAS = ("NativeNotificationItem", "NativeNotificationList", "NativeNotificationClaimOut", "NativeNotificationResultIn", "NativeNotificationResultOut")
RELATION_SCHEMAS = ("RelationUpdateIn", "RelationUpdateOut", "RelatedRecord")
AI_SCHEMAS = ("AISettings", "AISettingsPatch", "AIStatus", "AICredentialIn", "AICredentialAction", "SettingsOut", "SettingsPatch", "AIInterpretation", "Analysis", "BootstrapOut", "LeadDetail", "RestoreOut")


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_openapi(app):
    spec = app.openapi()
    spec["servers"] = [{"url": "http://127.0.0.1:{port}", "variables": {"port": {"default": "0"}}}]
    spec.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    spec["security"] = [{"bearer": []}]
    dump(OUT / "openapi.json", spec)


def drain(ctx, kinds) -> None:  # type: ignore[no-untyped-def]
    w = Worker(ctx, "export", kinds)
    while w.run_once():
        pass


def native_fixtures(ctx, c) -> None:  # type: ignore[no-untyped-def]
    # Actual private API responses from a disposable DB. Preserve all unrelated
    # fixtures during the incremental --native-only contract export.
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": False}})
        row = emit(s, "notification.created", {"kind": "new_lead", "title": "합성 알림 원문"}, notify=True)
        row.created_at = datetime.now(UTC)
        id_ = row.id
    dump(OUT / "fixtures" / "native-notifications.json", c.get("/v1/native-notifications").json())
    claimed = c.post(f"/v1/native-notifications/{id_}/claim").json()
    dump(OUT / "fixtures" / "native-notification-claim.json", claimed)
    dump(OUT / "fixtures" / "native-notification-result.json", c.post(f"/v1/native-notifications/{id_}/result", json={"state": "unknown", "claim_token": claimed["claim_token"]}).json())


def native_only() -> None:
    with tempfile.TemporaryDirectory(prefix="worklead-native-contracts-") as tmp:
        ctx = make_ctx(Path(tmp), dev=True)
        try:
            c = client_for(ctx)
            spec = c.app.openapi()
            spec["servers"] = [{"url": "http://127.0.0.1:{port}", "variables": {"port": {"default": "0", "description": "실행마다 바뀌는 임의 포트"}}}]
            spec.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer", "description": "실행마다 바뀌는 토큰 (Tauri 브리지로 전달)"}}
            spec["security"] = [{"bearer": []}]
            dump(OUT / "openapi.json", spec)
            for name in NATIVE_SCHEMAS:
                dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
            native_fixtures(ctx, c)
        finally:
            ctx.db.dispose()
    print("native contracts written; existing fixtures preserved")


def relations_only() -> None:
    """Add actual relation API fixtures without rewriting existing fixtures."""
    from tests.test_source_fields import sample
    from worklead.engine.pipeline import ingest

    with tempfile.TemporaryDirectory(prefix="worklead-relations-contracts-") as tmp:
        ctx = make_ctx(Path(tmp), dev=True)
        try:
            with ctx.db.session() as s:
                ids = []
                for suffix in ("relation-a", "relation-b"):
                    post = sample()
                    post.source_post_id = suffix
                    post.original_url = post.canonical_url = "https://jobs.daangn.com/job-posts/" + suffix
                    ids.append(ingest(s, "daangn-alba", post, parser_version="fixture", now=datetime.now(UTC)).lead_id)
            with client_for(ctx) as c:
                spec = c.app.openapi()
                spec["servers"] = [{"url": "http://127.0.0.1:{port}", "variables": {"port": {"default": "0", "description": "실행마다 바뀌는 임의 포트"}}}]
                spec.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer", "description": "실행마다 바뀌는 토큰 (Tauri 브리지로 전달)"}}
                spec["security"] = [{"bearer": []}]
                dump(OUT / "openapi.json", spec)
                for name in RELATION_SCHEMAS:
                    dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
                first = c.get(f"/v1/leads/{ids[0]}").json()
                dump(OUT / "fixtures" / "relation-candidate.json", first)
                related = first["related"][0]
                for action, name in (("confirm", "relation-confirmed"), ("dismiss", "relation-dismissed")):
                    response = c.post(f"/v1/leads/{ids[0]}/relations/{related['id']}", json={
                        "action": action, "expected_revision": related["revision"],
                        "expected_generation": related["database_generation"],
                    })
                    response.raise_for_status()
                    payload = response.json()
                    dump(OUT / "fixtures" / f"{name}.json", payload)
                    related = payload["lead"]["related"][0]
        finally:
            ctx.db.dispose()
    print("relation contracts written; existing fixtures preserved")


def ai_only(*, schemas_only: bool = False) -> None:
    """Generate additive AI contracts without changing original demo fixtures."""
    from worklead.services.credentials import FakeCredentialStore
    with tempfile.TemporaryDirectory(prefix="worklead-ai-contracts-") as tmp:
        ctx = make_ctx(Path(tmp), dev=True)
        try:
            ctx.credential_store = FakeCredentialStore()
            with client_for(ctx) as c:
                spec = c.app.openapi()
                spec["servers"] = [{"url": "http://127.0.0.1:{port}", "variables": {"port": {"default": "0", "description": "실행마다 바뀌는 임의 포트"}}}]
                spec.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer", "description": "실행마다 바뀌는 토큰 (Tauri 브리지로 전달)"}}
                spec["security"] = [{"bearer": []}]
                dump(OUT / "openapi.json", spec)
                for name in AI_SCHEMAS:
                    dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
                if schemas_only:
                    return
                state = c.get("/v1/ai/status")
                state.raise_for_status()
                dump(OUT / "fixtures" / "ai-status-unconfigured.json", state.json())
                pre = state.json()
                # Synthetic store only. No actual OS key and no API call.
                response = c.post("/v1/ai/credentials", json={"key": "synthetic-contract-key", "expected_revision": pre["settings_revision"], "expected_generation": pre["database_generation"]})
                response.raise_for_status()
                dump(OUT / "fixtures" / "ai-status-configured.json", response.json())
                pre = response.json()
                response = c.post("/v1/ai/credentials/delete", json={"expected_revision": pre["settings_revision"], "expected_generation": pre["database_generation"]})
                response.raise_for_status()
                dump(OUT / "fixtures" / "ai-status-deleted.json", response.json())
        finally:
            ctx.db.dispose()
    print("AI contracts written; existing fixtures preserved")


def jobs_only() -> None:
    """Export only the new draft/message contracts; keep old compatibility fixtures."""
    from tests.test_job_applications import post, NOW
    from worklead.engine.pipeline import ingest
    from worklead.jobs.messages import MessageStatus
    from worklead.api.job_message_routes import StartMessageWatch
    with tempfile.TemporaryDirectory(prefix="worklead-jobs-contracts-") as tmp:
        ctx = make_ctx(Path(tmp), dev=True)
        ctx.clock = lambda: NOW
        try:
            with ctx.db.session() as s:
                s.get(SourcePolicy, "daangn-alba").status = "allowed"
                id_ = ingest(s, "daangn-alba", post(), parser_version="synthetic", now=NOW).lead_id
            c = client_for(ctx)
            write_openapi(c.app)
            for model in (S.JobApplicationCandidate, S.JobApplicationHold, S.JobApplicationList, S.JobApplicationDraftPatch,
                          MessageStatus, StartMessageWatch, S.SettingsOut, S.SettingsPatch):
                dump(OUT / "schemas" / f"{model.__name__}.schema.json", model.model_json_schema())
            dump(OUT / "fixtures/job-applications.json", c.get("/v1/jobs/applications").json())
            dump(OUT / "fixtures/job-message-status.json", c.get("/v1/jobs/messages/status").json())
            draft = c.post(f"/v1/jobs/applications/{id_}/prepare")
            draft.raise_for_status()
            dump(OUT / "fixtures/job-application-draft.json", draft.json())
            rejected = c.post(f"/v1/jobs/applications/{id_}/submit")
            assert rejected.status_code == 409 and rejected.json()["error"]["details"]["sent"] == 0
            dump(OUT / "fixtures/errors/application-submission-unavailable.json", rejected.json())
        finally:
            ctx.db.dispose()
    print("jobs contracts written; unrelated fixtures preserved; no browser or remote submission")


def main() -> None:
    if "--auto-apply-only" in sys.argv:
        from worklead.jobs.auto_apply_models import AutoApplyStartIn, AutoApplyStartOut, AutoApplyStatus, AutoAttemptOut, AutoConnection
        with tempfile.TemporaryDirectory(prefix="worklead-auto-apply-contracts-") as tmp:
            ctx = make_ctx(Path(tmp), dev=True)
            try:
                c = client_for(ctx)
                write_openapi(c.app)
                for model in (AutoApplyStartIn, AutoApplyStartOut, AutoApplyStatus, AutoAttemptOut, AutoConnection, S.SourcePatch):
                    dump(OUT / "schemas" / f"{model.__name__}.schema.json", model.model_json_schema())
                dump(OUT / "fixtures" / "auto-apply-stopped.json", c.get("/v1/jobs/auto-apply/status").json())
            finally:
                ctx.db.dispose()
        print("auto-apply contracts written; no pairing secret exported; unrelated fixtures preserved")
        return
    if "--chrome-only" in sys.argv:
        from worklead.jobs.messages import MessageStatus
        from worklead.api.job_chrome_routes import ChromeConnection, ChromeStart
        with tempfile.TemporaryDirectory(prefix="worklead-chrome-contracts-") as tmp:
            ctx = make_ctx(Path(tmp), dev=True)
            try:
                write_openapi(client_for(ctx).app)
                for model in (MessageStatus, ChromeConnection, ChromeStart):
                    dump(OUT / "schemas" / f"{model.__name__}.schema.json", model.model_json_schema())
                dump(OUT / "fixtures" / "chrome-message-waiting.json", MessageStatus(provider="chrome", state="starting", enabled=True,
                     message="크롬 확장 연결 대기 중입니다. 선택한 당근 탭을 연결하세요").model_dump(mode="json"))
            finally:
                ctx.db.dispose()
        print("chrome contracts written; no pairing secret or unrelated fixture exported")
        return
    if "--cafe-only" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="worklead-cafe-contracts-") as tmpdir:
            ctx = make_ctx(Path(tmpdir), dev=True)
            try:
                with client_for(ctx) as c:
                    write_openapi(c.app)
                    for name in ("CafeSnapshot", "CafeSettings", "CafeSettingsPatch", "CafeConsentIn", "CafeConsentOut", "CafeEntry", "CafeBoardOut", "CafeJob", "CafeList", "CafeJobList", "CafeTemplate", "CafeTemplatePatch", "CafeAttempt", "CafeAttemptList"):
                        dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
                    dump(OUT / "fixtures/cafe-status.json", c.get("/v1/cafe/status").json())
                    dump(OUT / "fixtures/cafes.json", c.get("/v1/cafe/cafes").json())
                    dump(OUT / "fixtures/cafe-template.json", c.get('/v1/cafe/template').json())
                    dump(OUT / "fixtures/cafe-attempts.json", c.get('/v1/cafe/attempts').json())
                    for name in ('CafeCommentOut', 'CafeCommentList', 'CafeCommentPatch', 'CafeCommentDraft', *NATIVE_SCHEMAS):
                        dump(OUT / 'schemas' / f'{name}.schema.json', getattr(S, name).model_json_schema())
                    dump(OUT / 'fixtures/cafe-comments.json', c.get('/v1/cafe/comments').json())
            finally:
                ctx.db.dispose()
        print("cafe contracts written; legacy fixtures preserved")
        return
    if "--ai-only" in sys.argv or "--ai-schema-only" in sys.argv:
        ai_only(schemas_only="--ai-schema-only" in sys.argv)
        return
    if "--relations-only" in sys.argv:
        relations_only()
        return
    if "--jobs-only" in sys.argv:
        jobs_only()
        return
    if "--native-only" in sys.argv:
        native_only()
        return
    tmp = Path(tempfile.mkdtemp(prefix="worklead-contracts-"))
    try:
        # ── OpenAPI ─────────────────────────────────────────────────
        ctx = make_ctx(tmp / "demo", demo=True, dev=True)
        seed(ctx)
        c = client_for(ctx)
        spec = c.app.openapi()
        spec["servers"] = [{"url": "http://127.0.0.1:{port}", "variables": {"port": {"default": "0", "description": "실행마다 바뀌는 임의 포트"}}}]
        spec.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer", "description": "실행마다 바뀌는 토큰 (Tauri 브리지로 전달)"}}
        spec["security"] = [{"bearer": []}]
        dump(OUT / "openapi.json", spec)

        # ── JSON Schema ────────────────────────────────────────────
        # Incremental feature fixtures (backup, sales, AI) must survive a full export.
        for name in ("LeadSummary", "LeadDetail", "LeadPage", "QueueCounts", "SourceOut", "RunOut", "SettingsOut", "BootstrapOut", "MetricsOut", "ErrorResponse", "JobAccepted", "HealthOut", "BackupOut", "BackupList", "BackupInspection", "RestoreIn", "RestoreOut"):
            dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
        for name in NATIVE_SCHEMAS:
            dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
        for name in RELATION_SCHEMAS:
            dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
        for name in AI_SCHEMAS:
            dump(OUT / "schemas" / f"{name}.schema.json", getattr(S, name).model_json_schema())
        dump(
            OUT / "schemas" / "Event.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Event",
                "description": "SSE data. id: 이벤트 ID, seq: 이어받기 순서값, at: 발생 시각(UTC), type 별 참조 ID 포함",
                "type": "object",
                "required": ["id", "seq", "type", "at"],
                "properties": {
                    "id": {"type": "string"},
                    "seq": {"type": "integer"},
                    "at": {"type": "string", "format": "date-time"},
                    "type": {
                        "enum": [
                            "run.progress",
                            "run.state_changed",
                            "lead.created",
                            "lead.updated",
                            "source.health_changed",
                            "analysis.completed",
                            "notification.created",
                            "stream.reset",
                        ]
                    },
                    "run_id": {"type": "string"},
                    "lead_id": {"type": "string"},
                    "source_id": {"type": ["string", "null"]},
                },
                "additionalProperties": True,
            },
        )

        # ── fixtures: 데모 ─────────────────────────────────────────
        fdir = OUT / "fixtures"
        fdir.mkdir(parents=True, exist_ok=True)
        dump(fdir / "bootstrap.json", c.get("/v1/bootstrap").json())
        dump(fdir / "health.json", c.get("/v1/health").json())
        dump(fdir / "settings.json", c.get("/v1/settings").json())
        dump(fdir / "metrics.json", c.get("/v1/metrics").json())
        dump(fdir / "sources.first_run.json", c.get("/v1/sources").json())
        dump(fdir / "queue-counts.json", c.get("/v1/leads/queue-counts", params={"recruit": "not_closed"}).json())
        for q in ("recommended", "needs_review", "active", "auto_excluded", "all"):
            dump(fdir / f"leads.{q}.json", c.get("/v1/leads", params={"queue": q, "limit": 50}).json())
        all_items = c.get("/v1/leads", params={"queue": "all", "limit": 100}).json()["items"]
        for i, item in enumerate(all_items):
            dump(fdir / "lead-details" / f"{i:02d}.json", c.get(f"/v1/leads/{item['id']}").json())
        dump(fdir / "leads.empty.json", c.get("/v1/leads", params={"keyword": "존재하지않는검색어"}).json())
        errs = {
            "policy_not_allowed": c.post("/v1/runs", json={"source_id": "daangn-alba", "kind": "discovery"}).json(),
            "unauthorized": c.get("/v1/health", headers={"Authorization": "Bearer x"}).json(),
            "validation_error": c.get("/v1/leads", params={"limit": 0}).json(),
            "not_found": c.get("/v1/leads/none").json(),
        }
        for k, v in errs.items():
            dump(fdir / "errors" / f"{k}.json", v)
        # 데모 이벤트 샘플 (seq 순)
        import asyncio

        async def sample() -> list[dict]:
            from starlette.requests import Request

            route = next(r for r in c.app.routes if getattr(r, "path", "") == "/v1/events")

            async def receive():  # type: ignore[no-untyped-def]
                await asyncio.sleep(3600)

            resp = await route.endpoint(Request({"type": "http", "method": "GET", "path": "/v1/events", "headers": [], "query_string": b"", "app": c.app}, receive), after_seq=0)  # type: ignore[attr-defined]
            out: list[dict] = []
            async for chunk in resp.body_iterator:
                if chunk.startswith(b"id:"):
                    data = chunk.decode().split("data: ", 1)[1]
                    out.append(json.loads(data))
                if len(out) >= 12:
                    break
            return out

        dump(fdir / "events.sample.json", asyncio.run(sample()))
        native_fixtures(ctx, c)
        ctx.db.dispose()

        # ── fixtures: 수집 시나리오 (합성 사이트 + 실제 엔진) ───────────
        scenarios = {
            "normal": fx.SiteBehavior(),
            "partial": fx.SiteBehavior(region_mode={"busan-suyeong": "429"}),
            "blocked": fx.SiteBehavior(region_mode={"seoul-mapo": "403"}),
            "parse_failure": fx.SiteBehavior(region_mode={"seoul-gangnam": "broken"}),
        }
        for name, behavior in scenarios.items():
            ad = fx.adapter(region_status="unverified")
            sctx = make_ctx(tmp / f"sc-{name}", adapters={ad.source_id: ad, "manual": ManualAdapter()})
            sctx.transport = fx.transport(behavior)
            with sctx.db.session() as s:
                pol = s.get(SourcePolicy, ad.source_id)
                pol.status, pol.basis = "allowed", "합성 테스트 사이트"
                put_setting(s, "query_groups", {"version": 2, "groups": [{"id": "all", "label": "테스트", "enabled": True, "keywords": ["홈페이지"]}]})
                runs.enqueue(s, "discovery", source_id=ad.source_id)
            drain(sctx, runs.COLLECTION_KINDS)
            sc = client_for(sctx)
            dump(fdir / "scenarios" / name / "sources.json", sc.get("/v1/sources").json())
            dump(fdir / "scenarios" / name / "runs.json", sc.get("/v1/runs").json())
            dump(fdir / "scenarios" / name / "leads.json", sc.get("/v1/leads", params={"limit": 50}).json())
            sctx.db.dispose()
        relations_only()
        print(f"contracts written to {OUT}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _ = select


if __name__ == "__main__":
    main()
