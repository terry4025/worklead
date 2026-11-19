"""2026-11-07: the user finished business registration. Posts that require a
business registration or a tax invoice are a conflict only when the profile
does not satisfy the requirement; for a registered user they are a strength.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from worklead.analysis.rules import business_requirements
from worklead.analysis.service import analyze
from worklead.analysis.types import DateInfo, PayInfo, PostInput, Profile
from worklead.engine.pipeline import ingest
from worklead.jobs.auto_apply import condition
from worklead.models import Lead, SourcePolicy
from worklead.services.settings import update_profile
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
SERVICES = ["website", "landing", "shop", "fullstack", "software", "vba", "automation"]
PROFILE = Profile(services=SERVICES)
TITLE = "쇼핑몰 홈페이지 수정 의뢰"
BODY = ("운영 중인 쇼핑몰 홈페이지 수정 의뢰합니다. 재택으로 진행 가능하며 전 과정 비대면입니다. "
        "작업 범위는 메인 화면 배너 교체와 상품 상세 레이아웃 수정입니다.")
REQUIRED = " 사업자 등록 필수, 세금계산서 발행 가능하신 분"
CAUTIONS = ("사업자 등록 요구 — 내 프로필은 미등록", "세금계산서 발행 요구 — 내 프로필은 발행 불가")
POSITIVES = ("사업자 요구 — 등록 완료", "세금계산서 요구 — 발행 가능")


@pytest.mark.parametrize("text,expected", [
    ("사업자 등록 필수입니다.", {"business"}),
    ("사업자등록증 있으신 분만 지원해주세요.", {"business"}),
    ("사업자 가능하신 분 구합니다.", {"business"}),
    ("사업자 소지자 우대합니다.", {"business"}),
    ("세금계산서 발행 필수입니다.", {"tax_invoice"}),
    ("세금계산서 발행 가능하신 분.", {"tax_invoice"}),
    ("세금 계산서 발행 요청합니다.", {"tax_invoice"}),
    ("사업자 등록 필수, 세금계산서 발행 가능하신 분", {"business", "tax_invoice"}),
    ("사업자 없어도 됩니다.", set()),
    ("사업자 없으셔도 가능하신 분 환영합니다.", set()),
    ("사업자 무관합니다.", set()),
    ("세금계산서 필요 없습니다.", set()),
    ("세금계산서 발행 안 해도 됩니다.", set()),
    ("홈페이지 제작 의뢰합니다.", set()),
])
def test_business_requirements_detector(text, expected):
    assert business_requirements(text) == expected


def run(body: str, profile: Profile):
    post = PostInput(title=TITLE, body=body, observed_at=NOW, source_status="open", last_checked_at=NOW,
                     published=DateInfo(NOW - timedelta(hours=1), "exact"), first_seen_at=NOW, collection_allowed=True,
                     pay_hint=PayInfo(raw="건당 2,000,000원", min=2_000_000, max=2_000_000, unit="project"))
    return analyze(post, profile, NOW, 24, None)


def test_unregistered_profile_puts_requirement_in_review():
    baseline = run(BODY, PROFILE)
    assert baseline.recommendation == "recommended", [r.text for r in baseline.reasons]
    result = run(BODY + REQUIRED, PROFILE)
    texts = [r.text for r in result.reasons]
    assert result.recommendation == "needs_review", texts
    assert all(c in texts for c in CAUTIONS), texts


def test_registered_profile_reads_requirement_as_strength():
    registered = replace(PROFILE, business_registered=True, tax_invoice=True)
    baseline = run(BODY, registered)
    result = run(BODY + REQUIRED, registered)
    texts = [r.text for r in result.reasons]
    assert result.recommendation == baseline.recommendation == "recommended", texts
    assert all(p in texts for p in POSITIVES), texts
    assert not any(c in texts for c in CAUTIONS)


def test_no_requirement_no_change():
    registered = replace(PROFILE, business_registered=True, tax_invoice=True)
    assert [r.text for r in run(BODY, registered).reasons] == [r.text for r in run(BODY, PROFILE).reasons]


@pytest.mark.parametrize("extra,flags,expected", [
    (REQUIRED, {}, "condition_conflict"),
    (REQUIRED, {"business_registered": True, "tax_invoice": True}, None),
    (" 사업자등록증 필수입니다.", {"business_registered": True, "tax_invoice": False}, None),
    (" 세금계산서 발행 필수입니다.", {"business_registered": True, "tax_invoice": False}, "condition_conflict"),
])
def test_condition_reads_profile_flags(ctx, extra, flags, expected):
    url = "https://jobs.daangn.com/job-posts/business-BUSINESS0001"
    post = ParsedPost(title=TITLE, body=BODY + extra, original_url=url, canonical_url=url, source_post_id="BUSINESS0001",
                      source_status="open", published=DateInfo(at=NOW - timedelta(hours=1), precision="exact"),
                      pay=PayInfo(raw="건당 2,000,000원", min=2_000_000, max=2_000_000, unit="project"))
    with ctx.db.session() as session:
        session.get(SourcePolicy, "daangn-alba").status = "allowed"
        update_profile(session, {"services": SERVICES, **flags})
        lead = session.get(Lead, ingest(session, "daangn-alba", post, parser_version="test", now=NOW).lead_id)
        assert condition(session, lead, NOW, 0) == expected


def test_settings_round_trip_and_reanalysis(client):
    profile = client.get("/v1/settings").json()["profile"]
    assert profile["business_registered"] is False and profile["tax_invoice"] is False
    response = client.patch("/v1/settings", json={"profile": {**profile, "business_registered": True, "tax_invoice": True}})
    assert response.status_code == 200, response.text
    got = client.get("/v1/settings").json()["profile"]
    assert got["business_registered"] is True and got["tax_invoice"] is True
    assert any(run["kind"] == "reanalyze_all" and run["state"] == "queued"
               for run in client.get("/v1/runs").json()["items"])
