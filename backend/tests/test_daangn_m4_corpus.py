"""Human-labeled synthetic discovery/classification corpus, without site access.

Run this file directly to print every missed label and per-domain confusion
counts. These are fixed-example checks, not a claim about market-wide recall
or a test of application submission. Title selection is intentionally measured
separately from full-text project eligibility.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from worklead.analysis.rules import classify_categories
from worklead.analysis.service import analyze
from worklead.analysis.types import DateInfo, PayInfo, PostInput, Profile
from worklead.engine.pipeline import ingest
from worklead.jobs.auto_apply import condition
from worklead.models import Lead, SourcePolicy
from worklead.services.settings import update_profile
from worklead.sources.base import ParsedPost
from worklead.sources.daangn import create

CORPUS_PATH = Path(__file__).parent / "fixtures" / "daangn_m4_corpus.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
NOW = datetime.fromisoformat(CORPUS["clock"].replace("Z", "+00:00"))

def evaluate_corpus() -> list[dict]:
    """Use the same exact clock, open state and services for every label."""
    adapter = create()
    query = json.dumps(CORPUS["query_keywords"], ensure_ascii=False)
    rows = []
    for case in CASES:
        amount = case["pay"]["amount"]
        pay = (
            PayInfo(raw="건당 200만원", min=amount, max=amount, unit=case["pay"]["unit"])
            if amount is not None
            else None
        )
        post = PostInput(
            title=case["title"],
            body=case["body"],
            observed_at=NOW,
            source_status="open",
            access_status="accessible",
            last_checked_at=NOW,
            published=DateInfo(at=NOW - timedelta(hours=1), precision="exact"),
            first_seen_at=NOW - timedelta(minutes=30),
            pay_hint=pay,
        )
        analysis = analyze(post, Profile(services=CORPUS["services"]), NOW)
        intent = analysis.judgements["demand_intent"].value
        engagement = analysis.judgements["engagement_type"].value
        # A scored lead still needs a buyer, a software deliverable and project
        # payment. This does not model Chrome/session/consent/submission gates.
        candidate = bool(
            analysis.dev_relevant
            and analysis.recommendation != "excluded"
            and analysis.total is not None
            and analysis.total >= CORPUS["threshold"]
            and intent in ("buyer_project", "buyer_ongoing")
            and engagement == "project"
            and analysis.pay.unit == "project"
        )
        route = adapter.saved_candidate_kind(case["title"], query) or "reject"
        rows.append(
            {
                "id": case["id"],
                "domain": case["domain"],
                "expected_target_buyer": case["expected_target_buyer"],
                "candidate": candidate,
                "score": analysis.total,
                "intent": intent,
                "engagement": engagement,
                "pay_unit": analysis.pay.unit,
                "dev_relevant": analysis.dev_relevant,
                "categories": analysis.categories,
                "recommendation": analysis.recommendation,
                "title_kind": route,
                "expected_title_kind": case["expected_title_kind"],
                "body_only": case["body_only"],
                "reasons": [reason.text for reason in analysis.reasons],
            }
        )
    return rows


def summarize_rows(rows: list[dict]) -> dict:
    """Account for every gold label, including rejected and body-only posts."""
    def confusion(items: list[dict]) -> dict:
        counts = Counter(
            "tp" if row["expected_target_buyer"] and row["candidate"] else
            "fn" if row["expected_target_buyer"] else
            "fp" if row["candidate"] else "tn"
            for row in items
        )
        return {key: counts[key] for key in ("tp", "fn", "fp", "tn")}

    counts = confusion(rows)
    buyers = [row for row in rows if row["expected_target_buyer"]]
    title_visible_buyers = [row for row in buyers if not row["body_only"]]
    selected = lambda items: sum(row["title_kind"] != "reject" for row in items)
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {
        "origin": "synthetic",
        "scope": "합성 고정 예제의 분류·발견 평가이며 실사이트의 전체 수집률이나 지원 성공을 검증하지 않는다.",
        "clock": CORPUS["clock"],
        "threshold": CORPUS["threshold"],
        "case_count": len(rows),
        "classification": {
            **counts,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "false_negative_ids": [row["id"] for row in buyers if not row["candidate"]],
            "false_positive_ids": [row["id"] for row in rows if row["candidate"] and not row["expected_target_buyer"]],
        },
        "title_discovery": {
            "buyer_count": len(buyers),
            "selected_buyers": selected(buyers),
            "title_visible_buyer_count": len(title_visible_buyers),
            "selected_title_visible_buyers": selected(title_visible_buyers),
            "missed_title_visible_buyer_ids": [row["id"] for row in title_visible_buyers if row["title_kind"] == "reject"],
            "body_only_buyer_ids": [row["id"] for row in buyers if row["body_only"]],
            "nonbuyer_selected_ids": [row["id"] for row in rows if not row["expected_target_buyer"] and row["title_kind"] != "reject"],
            "priority_label_differences": [
                {"id": row["id"], "expected": row["expected_title_kind"], "actual": row["title_kind"]}
                for row in rows if row["expected_title_kind"] != row["title_kind"]
            ],
        },
        "per_domain": {
            domain: confusion([row for row in rows if row["domain"] == domain])
            for domain in sorted({row["domain"] for row in rows})
        },
    }


@pytest.fixture(scope="module")
def rows_by_id() -> dict[str, dict]:
    return {row["id"]: row for row in evaluate_corpus()}


def test_corpus_contains_human_labels_and_service_boundaries() -> None:
    assert CORPUS["origin"] == "synthetic"
    assert CORPUS["threshold"] == 50
    assert 30 <= len(CASES) <= 50
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert {"website", "software", "automation", "vba", "education", "seller", "employment", "physical", "installation", "risky"} <= {case["domain"] for case in CASES}
    for case in CASES:
        assert case["origin"] == "synthetic"
        assert case["label_rationale"] and case["title"] and case["body"]
        assert case["expected_title_kind"] in {"core", "exploration", "reject"}
        assert isinstance(case["expected_target_buyer"], bool)
        assert case["expected_min_score"] == (50 if case["expected_target_buyer"] else None)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_full_text_target_buyer_label(case: dict, rows_by_id: dict[str, dict]) -> None:
    row = rows_by_id[case["id"]]
    assert row["candidate"] == case["expected_target_buyer"], row
    if case["expected_min_score"] is not None:
        assert row["score"] is not None and row["score"] >= case["expected_min_score"], row


TITLE_BUYER_CASES = [
    pytest.param(case, id=case["id"])
    for case in CASES if case["expected_target_buyer"] and not case["body_only"]
]


@pytest.mark.parametrize("case", TITLE_BUYER_CASES)
def test_title_visible_buyer_is_selected(case: dict, rows_by_id: dict[str, dict]) -> None:
    row = rows_by_id[case["id"]]
    # Core/exploration priority differences are fully reported but neither is
    # a missed discovery. Only reject blocks a meaningful titled request.
    assert row["title_kind"] in {"core", "exploration"}, row


@pytest.mark.parametrize("case_id", ["physical_packaging", "member_registration", "store_installation"])
def test_publication_addition_does_not_promote_nonsoftware(case_id: str, rows_by_id: dict[str, dict]) -> None:
    case = next(case for case in CASES if case["id"] == case_id)
    _, dev = classify_categories(case["title"], case["body"])
    assert not dev
    assert not rows_by_id[case_id]["candidate"]


@pytest.mark.parametrize(
    "title,queries,expected_selected",
    [
        ("React 화면 수정 도와주세요", ["홈페이지"], True),
        ("React 화면 수정 도와주세요", ["엑셀"], False),
        ("React 화면 수정 도와주세요", ["앱"], False),
        ("React 화면 수정 도와주세요", ["의뢰"], False),
        ("React 화면 수정 도와주세요", [], False),
        ("서버 배포 도와주실 분", ["프로그램"], True),
        ("서버 배포 도와주실 분", ["엑셀"], False),
        ("서버 배포 도와주실 분", ["홈페이지"], False),
        ("서버 배포 도와주실 분", [], False),
        ("구글 스프레드시트 연동 의뢰", ["스프레드시트"], True),
        ("구글 스프레드시트 연동 의뢰", ["엑셀"], False),
        ("앱스토어 등록 도와주실 분", ["앱"], True),
        ("앱스토어 등록 도와주실 분", ["엑셀"], False),
        ("React", ["홈페이지"], False),
        ("React 개발자 정규직 채용", ["홈페이지"], False),
        ("React 프로그램 설치 알바", ["홈페이지"], False),
        ("서버 배포 알바 구합니다", ["프로그램"], False),
        ("서버 배포 배우고 싶어요", ["프로그램"], False),
        ("앱스토어 회원 등록 도와주실 분", ["앱"], False),
        ("앱스토어에서 앱 설치 도와주세요", ["앱"], False),
        ("쇼핑몰 상품 패키징 의뢰", ["앱"], False),
    ],
)
def test_delivery_title_selection_respects_active_scope(title: str, queries: list[str], expected_selected: bool) -> None:
    route = create().saved_candidate_kind(title, json.dumps(queries, ensure_ascii=False))
    assert (route is not None) == expected_selected


@pytest.mark.parametrize(
    "title,body",
    [
        ("포장 의뢰", "웹사이트 앱 패키징 대신 물건 포장을 도와주세요."),
        ("포장 의뢰", "기존 앱 플레이스토어 등록은 필요 없고 상자 포장을 도와주세요."),
        ("일반 도움 요청", "홈페이지 앱 패키징 의뢰가 아닙니다."),
    ],
)
def test_publication_requires_software_operation_itself_to_be_requested(title: str, body: str) -> None:
    _, dev = classify_categories(title, body)
    assert not dev


PUBLICATION_POLARITY_CASES = [
    ("package-no-request", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰는 안 합니다.", False),
    ("package-not-do", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰하지 않습니다.", False),
    ("package-not-ask", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 요청은 안 합니다.", False),
    ("package-not-going-to", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 요청하지 않겠습니다.", False),
    ("package-no-please", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 부탁드리지 않습니다.", False),
    ("package-not-a-request", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰가 아닙니다.", False),
    ("package-no-plan", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰할 계획은 없습니다.", False),
    ("package-no-intention", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰할 생각이 없습니다.", False),
    ("package-unable", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰 못 합니다.", False),
    ("package-operation-denied", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징은 안 합니다.", False),
    ("package-no-helper", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 도와주실 분은 필요 없습니다.", False),
    ("package-short-body-refusal", "홈페이지 앱 패키징 의뢰", "앱 패키징 요청은 안 합니다.", False),
    ("package-short-operation-refusal", "홈페이지 앱 패키징 의뢰", "앱 패키징은 안 합니다.", False),
    ("package-bare-body-refusal", "홈페이지 앱 패키징 의뢰", "패키징 의뢰하지 않습니다.", False),
    ("package-title-denied", "홈페이지 앱 패키징 의뢰는 안 합니다", "이번에는 상자 포장만 의뢰합니다.", False),
    ("store-no-request", "기존 앱 플레이스토어 등록 의뢰", "기존 앱 플레이스토어 등록 요청은 안 합니다.", False),
    ("store-not-do", "기존 앱 플레이스토어 등록 의뢰", "기존 앱 플레이스토어 등록 의뢰하지 않습니다.", False),
    ("store-no-ask", "기존 앱 플레이스토어 등록 의뢰", "완성된 앱을 플레이스토어에 등록 요청하지 않습니다.", False),
    ("store-no-plan", "기존 앱 플레이스토어 등록 의뢰", "완성된 앱을 플레이스토어에 등록 의뢰할 계획이 없습니다.", False),
    ("store-no-need", "기존 앱 플레이스토어 등록 의뢰", "완성된 앱을 플레이스토어에 등록해주실 필요는 없습니다.", False),
    ("store-short-body-refusal", "기존 앱 플레이스토어 등록 의뢰", "앱스토어 등록 요청은 안 합니다.", False),
    ("store-bare-body-refusal", "기존 앱 플레이스토어 등록 의뢰", "등록 요청은 안 합니다.", False),
    ("store-bare-next-sentence-refusal", "기존 앱 플레이스토어 등록 의뢰", "이번에는 포장 작업입니다. 등록 의뢰하지 않습니다.", False),
    ("store-operation-denied", "기존 앱 플레이스토어 등록 의뢰", "플레이스토어 등록은 안 합니다.", False),
    ("package-positive", "홈페이지 앱 패키징 의뢰", "홈페이지 앱 패키징 의뢰합니다.", True),
    ("package-positive-help", "앱 작업 도움 요청", "기존 웹사이트의 앱 패키징을 도와주실 분을 찾습니다.", True),
    ("package-positive-change", "홈페이지 앱 패키징 의뢰", "홈페이지를 앱으로 패키징해주실 분을 찾습니다.", True),
    ("store-positive", "기존 앱 플레이스토어 등록 의뢰", "완성된 앱을 플레이스토어에 등록해주실 분을 찾습니다.", True),
    ("package-store-positive", "앱스토어 등록 도와주실 분", "기존 홈페이지를 앱으로 패키징하여 앱스토어 등록을 도와주실 분을 찾습니다.", True),
    ("mixed-physical-denied", "홈페이지 앱 패키징 의뢰", "종이 상자 포장은 안 합니다. 홈페이지 앱 패키징 의뢰합니다.", True),
    ("mixed-package-denied", "기존 앱 플레이스토어 등록 의뢰", "홈페이지 앱 패키징 의뢰는 안 합니다. 완성된 앱을 플레이스토어에 등록해주실 분을 찾습니다.", True),
    ("mixed-store-denied", "홈페이지 앱 패키징 의뢰", "기존 앱 플레이스토어 등록 요청은 안 합니다. 홈페이지 앱 패키징 의뢰합니다.", True),
    ("mixed-title-denied", "홈페이지 앱 패키징 의뢰는 안 합니다", "완성된 앱을 플레이스토어에 등록해주실 분을 찾습니다.", True),
]


@pytest.mark.parametrize(
    "case_id,title,body,expected_candidate",
    PUBLICATION_POLARITY_CASES,
    ids=[case[0] for case in PUBLICATION_POLARITY_CASES],
)
def test_publication_polarity_blocks_even_previously_scored_auto_apply_candidate(
    ctx, case_id: str, title: str, body: str, expected_candidate: bool,
) -> None:
    # This temporary test database does not connect to or modify operational
    # data. All source details are synthetic, fresh, open and paid per project.
    suffix = " 건당 200만원, 전 과정 비대면으로 진행하며 자료를 전달합니다."
    if not expected_candidate:
        suffix = " 종이 상자 포장 외주 맡기려고 합니다." + suffix
    body += suffix
    _, dev = classify_categories(title, body)
    assert dev == expected_candidate, case_id
    source_url = "https://jobs.daangn.com/job-posts/m4-publication-M4CASE000001"
    post = ParsedPost(
        title=title,
        body=body,
        original_url=source_url,
        canonical_url=source_url,
        source_post_id="M4CASE000001",
        source_status="open",
        published=DateInfo(at=NOW - timedelta(hours=1), precision="exact"),
        pay=PayInfo(raw="건당 200만원", min=2_000_000, max=2_000_000, unit="project"),
    )
    with ctx.db.session() as session:
        session.get(SourcePolicy, "daangn-alba").status = "allowed"
        update_profile(session, {"services": CORPUS["services"]})
        lead_id = ingest(session, "daangn-alba", post, parser_version="test-m4", now=NOW).lead_id
        lead = session.get(Lead, lead_id)
        # Simulate a previously queued, incorrectly scored buyer. The current
        # production condition must reanalyze the raw text and block it anyway.
        lead.priority_total = 90
        lead.recommendation = "needs_review"
        lead.demand_intent = "buyer_project"
        lead.engagement_type = "project"
        lead.pay_unit = "project"
        assert condition(session, lead, NOW, 50) == (None if expected_candidate else "condition_changed"), case_id
        assert condition(session, lead, NOW, 50, queue=True) == (None if expected_candidate else "condition_changed"), case_id


def test_report_accounts_for_every_label_and_domain(rows_by_id: dict[str, dict]) -> None:
    rows = list(rows_by_id.values())
    report = summarize_rows(rows)
    counts = report["classification"]
    assert sum(counts[key] for key in ("tp", "fn", "fp", "tn")) == len(CASES)
    assert sum(domain[key] for domain in report["per_domain"].values() for key in ("tp", "fn", "fp", "tn")) == len(CASES)
    # Inverting any single classification must affect precisely its semantic
    # positive/negative confusion bucket; no label may disappear from metrics.
    for index, row in enumerate(rows):
        changed = [dict(item) for item in rows]
        changed[index]["candidate"] = not row["candidate"]
        updated = summarize_rows(changed)["classification"]
        from_key, to_key = ("tp", "fn") if row["expected_target_buyer"] else ("fp", "tn")
        delta = -1 if row["candidate"] else 1
        assert updated[from_key] == counts[from_key] + delta
        assert updated[to_key] == counts[to_key] - delta


if __name__ == "__main__":
    print(json.dumps(summarize_rows(evaluate_corpus()), ensure_ascii=False, indent=2))
