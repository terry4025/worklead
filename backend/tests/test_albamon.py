"""알바몬 어댑터 검증 (합성 사이트 — tests/fixture_albamon.py). 실제 사이트를 호출하지 않는다."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from tests import fixture_albamon as fx
from tests.conftest import client_for, make_ctx
from worklead.engine import runs
from worklead.engine.worker import Worker
from worklead.models import Run, Snapshot, SourcePolicy, SourceRecord
from worklead.sources import albamon
from worklead.sources.manual import ManualAdapter

SID = "albamon"


def setup(tmp_path: Path, behavior: fx.Behavior | None = None, *, allow: bool = True):
    behavior = behavior or fx.Behavior()
    ad = albamon.create()
    ctx = make_ctx(tmp_path, adapters={SID: ad, "manual": ManualAdapter()})
    ctx.transport = fx.transport(behavior)
    if allow:
        with ctx.db.session() as s:
            pol = s.get(SourcePolicy, SID)
            pol.status, pol.basis = "allowed", "테스트 — 합성 사이트"
    return ctx, behavior


def run_discovery(ctx) -> Run:
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id=SID)
        run_id = run.id
    w = Worker(ctx, "collector", runs.COLLECTION_KINDS)
    while w.run_once():
        pass
    with ctx.db.session() as s:
        r = s.get(Run, run_id)
        assert r is not None
        return r


def test_policy_pending_by_default_blocks_runs(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path, allow=False)
    c = client_for(ctx)
    src = next(i for i in c.get("/v1/sources").json()["items"] if i["id"] == SID)
    assert src["policy"]["status"] == "permission_pending" and src["research"]["ready"] is True
    r = c.post("/v1/runs", json={"source_id": SID, "kind": "discovery"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "policy_not_allowed"
    assert behavior.requests == []


def test_lists_prefilter_details_and_judge_short_gigs(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path)
    run = run_discovery(ctx)
    assert run.state == "succeeded", (run.state, run.error_code, run.error_message)
    details = sorted(p.rsplit("/", 1)[-1] for p in behavior.requests if p.startswith("/jobs/detail/"))
    # 교육생 모집 광고·연봉제·IT 무관 재택(상담)·업직종 목록의 체험단 광고·같은 제목 반복 게시는 상세를 요청하지 않는다.
    # 기본값(건당·1회성 작업만)에서는 시급 공고(900004)도 목록에서 건너뛴다
    assert details == ["900001", "900006", "900008", "900010"]
    # robots 가 금지한 경로는 요청하지 않는다
    assert not any(p.startswith(("/jobs/detail-content", "/jobs/detail/content", "/jobs/apply")) for p in behavior.requests)

    c = client_for(ctx)
    items = {i["title"]: i for i in c.get("/v1/leads", params={"queue": "all"}).json()["items"]}
    gig = items["쇼핑몰 관리자 페이지 수정 (재택, 단기)"]
    assert gig["recommendation"] == "recommended", gig["reasons"]
    assert gig["work_mode"]["value"] == "fully_remote"
    assert gig["pay"]["unit"] == "project" and gig["pay"]["min"] == 300000
    excel = items["엑셀 반복작업 자동화 재택"]
    assert excel["recommendation"] == "recommended", excel["reasons"]
    # 재택근무 표시 없이 사업장 근무지만 있는 공고는 출근 근무로 추정해 자동 제외 (근거 표시)
    onsite = items["상품 정보 수집 프로그램 개발 알바"]
    assert onsite["recommendation"] == "excluded" and onsite["work_mode"] == {"value": "onsite", "basis": "inferred"}
    assert any("출근 근무로 보임" in r["text"] for r in onsite["reasons"])
    site = items["회사 홈페이지 리뉴얼 작업자 구합니다"]
    assert site["recommendation"] == "recommended", site["reasons"]

    with ctx.db.session() as s:
        rec = s.scalar(select(SourceRecord).where(SourceRecord.source_post_id == "900001"))
        assert rec is not None and rec.workplace_raw == "재택근무"
        body = s.scalar(select(Snapshot.body_text).where(Snapshot.source_record_id == rec.id))
        # 담당자 연락처·도로명 주소는 저장하지 않는다
        assert "010-1234-5678" not in body and "owner@example.com" not in body and "010-9999-0000" not in body
        assert "합성로" not in (rec.posted_region_raw or "") and "합성로" not in body

    detail = c.get(f"/v1/leads/{gig['id']}").json()
    assert "쇼핑몰 관리자 페이지 수정" in detail["quick_message"]


def test_broken_list_is_parse_failure(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.Behavior(list_mode={"part-9060": "broken"}))
    run = run_discovery(ctx)
    assert run.state == "partial"
    assert run.counts["parse_failures"] >= 1


def test_gig_only_off_also_checks_hourly_posts(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path)
    c = client_for(ctx)
    prof = {**c.get("/v1/settings").json()["profile"], "gig_only": False}
    assert c.patch("/v1/settings", json={"profile": prof}).status_code == 200
    run = run_discovery(ctx)
    assert run.state == "succeeded"
    details = sorted(p.rsplit("/", 1)[-1] for p in behavior.requests if p.startswith("/jobs/detail/"))
    assert "900004" in details
    items = {i["title"]: i for i in c.get("/v1/leads", params={"queue": "all"}).json()["items"]}
    hourly = items["웹개발 코딩 알바"]
    assert hourly["recommendation"] == "excluded"  # 사무실 출근 (시급이라서가 아님)
    assert not any("시급 보수" in r["text"] for r in hourly["reasons"])

