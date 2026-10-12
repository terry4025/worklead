"""Recruitment dates must not inherit delivery or future status semantics."""
from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from worklead.analysis.dates import closed_marker, parse_deadline, parse_relative_published, parse_work_start
from worklead.engine.pipeline import ingest
from worklead.models import Lead, SourceRecord
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize("wording", [
    "모집 마감은 10월 5일입니다.",
    "모집 마감일: 10월 5일",
    "모집은 10월 5일 마감합니다.",
    "내일 모집 마감합니다.",
    "채용 완료 시 마감됩니다.",
    "모집 종료 예정입니다.",
    "채용 완료 후 연락드립니다.",
    "채용 완료되지 않았습니다.",
    "모집 마감이 아닙니다.",
    "[마감]으로 바뀌면 연락해주세요.",
    "[마감]으로 표시되면 지원할 수 없습니다.",
])
def test_future_conditional_and_negated_notices_are_not_closed(wording):
    assert closed_marker(wording, "홈페이지 제작 의뢰") is None
    assert closed_marker("", wording) is None


@pytest.mark.parametrize("wording", [
    "[마감] 홈페이지 제작 의뢰",
    "모집 마감",
    "모집 마감되었습니다.",
    "모집 마감이 되었습니다.",
    "모집 마감하였습니다.",
    "모집 마감 완료",
    "모집 마감합니다.",
    "마감했습니다.",
    "모집이 마감됐습니다.",
    "모집은 9월 27일 마감했습니다.",
    "채용 완료",
    "구인 완료",
    "모집 종료",
])
def test_explicit_current_and_completed_closed_notices_are_preserved(wording):
    assert closed_marker(wording, "") is not None
    assert closed_marker("", wording) is not None


@pytest.mark.parametrize("wording", [
    "작업 완료는 9월 27일까지 부탁합니다.",
    "홈페이지 납품은 9월 27일까지입니다.",
    "납기: 9월 27일까지",
    "프로젝트 마감일: 9월 27일",
    "근무 기간은 9월 27일까지입니다.",
    "오픈은 9월 27일까지 해야 합니다.",
    "배포 마감은 9월 27일입니다.",
    "9월 27일까지 부탁합니다.",
])
def test_delivery_work_and_unqualified_dates_are_not_recruitment_deadlines(wording):
    deadline = parse_deadline(wording, NOW)
    assert deadline.at is None and deadline.raw is None


@pytest.mark.parametrize("wording", [
    "작업 마감했습니다.",
    "프로젝트 마감 완료",
    "작업을 마감하였습니다.",
    "납품 마감되었습니다.",
])
def test_completed_work_is_not_completed_recruitment(wording):
    assert closed_marker(wording, "홈페이지 제작 의뢰") is None
    assert closed_marker("", wording) is None


def test_separate_recruitment_and_delivery_clauses_keep_current_closed_status():
    assert closed_marker("모집 마감했습니다. 프로젝트 납품은 다음 주입니다.", "") is not None


@pytest.mark.parametrize("wording", [
    "10월 5일까지 지원 받습니다.",
    "지원 마감일: 10월 5일",
    "모집 마감은 10월 5일입니다.",
    "모집은 10월 5일 마감합니다.",
    "10/5까지 접수 받습니다.",
    "마감일: 10월 5일",
])
def test_explicit_recruitment_dates_and_bare_deadline_labels_keep_day_precision(wording):
    deadline = parse_deadline(wording, NOW)
    assert deadline.at == datetime(2026, 10, 4, 15, tzinfo=UTC)
    assert deadline.precision == "day"
    ev = deadline.evidence[0]
    assert wording[ev.start:ev.end] == ev.quote


def test_delivery_date_before_recruitment_date_does_not_mask_the_valid_deadline():
    body = "작업 완료는 10월 3일까지 부탁합니다. 지원은 10월 5일까지 받습니다."
    assert parse_deadline(body, NOW).at == datetime(2026, 10, 4, 15, tzinfo=UTC)


def test_dates_in_separate_clauses_keep_their_purpose():
    body = "지원은 10월 5일까지, 홈페이지 납품은 10월 20일까지입니다."
    assert parse_deadline(body, NOW).at == datetime(2026, 10, 4, 15, tzinfo=UTC)


def test_new_year_rollover_work_start_and_relative_published_precision_are_preserved():
    observed = datetime(2026, 12, 30, tzinfo=UTC)
    assert parse_deadline("1월 5일까지 지원 받습니다.", observed).at == datetime(2027, 1, 4, 15, tzinfo=UTC)
    start = parse_work_start("1월 10일부터 시작합니다.", observed)
    assert start.at == datetime(2027, 1, 9, 15, tzinfo=UTC) and start.precision == "day"
    published = parse_relative_published("3일 전", observed)
    assert published.at == datetime(2026, 12, 27, tzinfo=UTC) and published.precision == "approximate"


@pytest.mark.parametrize("tail", [
    "모집 마감은 10월 5일입니다.",
    "작업 완료는 9월 27일까지 부탁합니다.",
])
def test_date_notices_do_not_close_a_verified_open_listing(tmp_path, tail):
    ctx = make_ctx(tmp_path)
    try:
        post = ParsedPost(title="홈페이지 제작 의뢰",
                          body="홈페이지 제작 의뢰합니다. 전 과정 비대면 진행. 예산 80만원.\n" + tail,
                          original_url=None, canonical_url=None, source_post_id="date-notice", source_status="open")
        with ctx.db.session() as s:
            result = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
            lead, record = s.get(Lead, result.lead_id), s.get(SourceRecord, result.record_id)
            assert record.source_status == "open"
            assert lead.recommendation == "recommended"
    finally:
        ctx.db.dispose()


def test_explicit_expired_recruitment_date_still_closes_the_listing(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = ParsedPost(title="홈페이지 제작 의뢰",
                          body="홈페이지 제작 의뢰합니다. 전 과정 비대면 진행. 예산 80만원.\n9월 27일까지 지원 받습니다.",
                          original_url=None, canonical_url=None, source_post_id="expired-recruitment", source_status="open")
        with ctx.db.session() as s:
            result = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
            assert s.get(SourceRecord, result.record_id).source_status == "closed"
            assert s.get(Lead, result.lead_id).recommendation == "excluded"
    finally:
        ctx.db.dispose()
