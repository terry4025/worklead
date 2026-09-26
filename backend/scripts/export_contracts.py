"""contracts/ 생성: openapi.json, schemas/, fixtures/ (실제 API 응답을 데모·합성 사이트로 생성).

사용: uv run python scripts/export_contracts.py
fixtures 는 response_model 직렬화를 거친 실제 응답이므로 스키마와 일치한다 (tests/test_contracts.py 가 재검증).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from tests import fixture_site as fx  # noqa: E402
from tests.conftest import client_for, make_ctx  # noqa: E402
from worklead.api import schemas as S  # noqa: E402
from worklead.engine import runs  # noqa: E402
from worklead.engine.worker import Worker  # noqa: E402
from worklead.models import SourcePolicy  # noqa: E402
from worklead.services.demo import seed  # noqa: E402
from worklead.services.settings import put_setting  # noqa: E402
from worklead.sources.manual import ManualAdapter  # noqa: E402

OUT = ROOT / "contracts"


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def drain(ctx, kinds) -> None:  # type: ignore[no-untyped-def]
    w = Worker(ctx, "export", kinds)
    while w.run_once():
        pass


def main() -> None:
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
        shutil.rmtree(OUT / "schemas", ignore_errors=True)
        for name in ("LeadSummary", "LeadDetail", "LeadPage", "QueueCounts", "SourceOut", "RunOut", "SettingsOut", "BootstrapOut", "MetricsOut", "ErrorResponse", "JobAccepted", "HealthOut"):
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
        shutil.rmtree(fdir, ignore_errors=True)
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
        print(f"contracts written to {OUT}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _ = select


if __name__ == "__main__":
    main()
