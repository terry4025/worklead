"""Cafe state and durable work. Unsupported ownership/comments fail closed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from ..api import schemas as S
from ..models import AppSetting, Cafe, CafeBoard, CafeConsent, CafeJob, CafeSession, SourcePolicy, new_id
from ..engine.events import emit, notify

CONSENT_VERSION = "cafe-risk-2026-10-12-v1"
CONSENT_TEXT = "당근 이용약관은 자동화 도구 사용에 사전 서면 동의를 요구하며, 반복 홍보글은 계정 제재 사유가 될 수 있습니다. 확인은 당근의 허가를 대신하지 않습니다. 직접 로그인한 전용 Edge 프로필로만 동작하고, 인증·차단 화면에서는 멈춥니다. 게시 대상과 규칙은 사용자가 확인해야 합니다."
KST = timezone(timedelta(hours=9))


class CafeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def day_key(now: datetime) -> str:
    return now.astimezone(KST).date().isoformat()


def canonical_cafe_url(url: str, *, board: bool = False) -> str:
    from ..cafe.parsing import safe_cafe_url, CafePageError
    try:
        url = safe_cafe_url(url.strip(), home_only=not board)
    except CafePageError as exc:
        raise CafeError(422, exc.code, str(exc)) from None
    try:
        value = urlsplit(url.strip())
        parts = [unquote(part) for part in value.path.split("/") if part]
        valid = len(parts) == 1 if not board else len(parts) == 3 and parts[1] == "boards"
        if (value.scheme != "https" or value.netloc.lower() != "cafe.daangn.com" or value.query or value.fragment
                or not valid or parts[0] in {"api", "debug", "errors", "categories", "-"}
                or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts)):
            raise ValueError
    except (ValueError, IndexError):
        raise CafeError(422, "invalid_cafe_url", "https://cafe.daangn.com/카페이름 형식의 주소를 입력하세요") from None
    return "https://cafe.daangn.com/" + "/".join(parts)


def get_settings(s: Session) -> dict[str, Any]:
    row = s.get(AppSetting, "cafe")
    return S.CafeSettings.model_validate(row.value if row else {}).model_dump()


def update_settings(s: Session, changes: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg = S.CafeSettings.model_validate({**get_settings(s), **{k: v for k, v in changes.items() if v is not None}}).model_dump()
    except ValidationError:
        raise CafeError(422, "invalid_cafe_settings", "카페 검색어와 한도 설정을 확인하세요") from None
    if cfg["auto_post"]:
        raise CafeError(409, "cafe_posting_unsupported", "내 글 대조와 게시 허용 근거를 확보하기 전에는 자동 게시를 켤 수 없습니다")
    row = s.get(AppSetting, "cafe")
    if row is None:
        s.add(AppSetting(key="cafe", value=cfg))
    else:
        row.value = cfg
    return cfg


def consent_accepted(s: Session) -> bool:
    return s.get(CafeConsent, CONSENT_VERSION) is not None


def accept_consent(s: Session, version: str, now: datetime) -> S.CafeConsentOut:
    if version != CONSENT_VERSION:
        raise CafeError(409, "cafe_consent_changed", "최신 위험 안내를 다시 확인하세요")
    row = s.get(CafeConsent, version)
    if row is None:
        row = CafeConsent(version=version, accepted_at=now)
        s.add(row)
    policy = s.get(SourcePolicy, "daangn_cafe")
    if policy:
        policy.status, policy.basis = "allowed", f"사용자 확인 {now.astimezone(KST).date()}"
        policy.reviewed_at, policy.reviewed_by = now, "user"
    return S.CafeConsentOut(version=row.version, accepted_at=row.accepted_at)


def session_row(s: Session) -> CafeSession:
    row = s.get(CafeSession, 1)
    if row is None:
        row = CafeSession(id=1, state="logged_out", usage_day="", page_count=0, post_count=0)
        s.add(row)
        s.flush()
    return row


def job_out(row: CafeJob) -> S.CafeJob:
    return S.CafeJob.model_validate({key: getattr(row, key) for key in S.CafeJob.model_fields})


def get_status(s: Session, now: datetime) -> S.CafeSnapshot:
    row, cfg, consent = session_row(s), get_settings(s), s.get(CafeConsent, CONSENT_VERSION)
    active = s.scalar(select(CafeJob).where(CafeJob.state.in_(("queued", "running"))).order_by(CafeJob.created_at).limit(1))
    return S.CafeSnapshot(consent=S.CafeConsentOut(version=consent.version, accepted_at=consent.accepted_at) if consent else None,
        consent_text=CONSENT_TEXT, consent_version=CONSENT_VERSION,
        session=S.CafeSessionOut(state=row.state, checked_at=row.checked_at, reason=row.reason),
        usage=S.CafeUsage(day=day_key(now), page_count=row.page_count if row.usage_day == day_key(now) else 0,
            post_count=row.post_count if row.usage_day == day_key(now) else 0, daily_page_limit=cfg["daily_page_limit"], daily_post_limit=cfg["daily_post_limit"]),
        settings=S.CafeSettings(**cfg), capabilities=S.CafeCapabilities(), active_job=job_out(active) if active else None)


def set_session(s: Session, state: str, now: datetime, reason: str | None = None) -> None:
    if state not in {"logged_in", "logged_out", "needs_user"}:
        raise ValueError("invalid cafe session state")
    row = session_row(s)
    changed = row.state != state
    row.state, row.checked_at, row.reason = state, now, reason
    if changed:
        emit(s, "cafe.session_changed", {"state": state})
        if state == "needs_user":
            notify(s, "source_issue", "당근 카페에서 직접 확인이 필요합니다", source_id="daangn_cafe", now=now,
                dedupe_key=f"cafe-needs-user-{now.isoformat()}")


def reserve_navigation(s: Session, now: datetime) -> float:
    if not consent_accepted(s):
        raise CafeError(409, "cafe_consent_required", "카페 기능 위험 안내를 먼저 확인하세요")
    row, cfg = session_row(s), get_settings(s)
    if row.state == "needs_user":
        raise CafeError(409, "cafe_needs_user", "인증·차단 화면을 직접 확인하세요")
    if row.usage_day != day_key(now):
        row.usage_day, row.page_count, row.post_count = day_key(now), 0, 0
    if row.page_count >= cfg["daily_page_limit"]:
        raise CafeError(409, "cafe_daily_budget", "오늘 카페 페이지 이동 한도에 도달했습니다")
    wait = max(0.0, cfg["min_navigation_seconds"] - (now - row.last_navigation_at).total_seconds()) if row.last_navigation_at else 0.0
    if wait == 0:
        row.page_count += 1
        row.last_navigation_at = now
    return wait


def enqueue_job(s: Session, kind: str, now: datetime, input: dict | None = None) -> CafeJob:
    if kind not in {"check", "login", "discover", "inspect", "collect", "post", "comments"}:
        raise CafeError(422, "invalid_cafe_action", "지원하지 않는 카페 작업입니다")
    if not consent_accepted(s):
        raise CafeError(409, "cafe_consent_required", "카페 기능 위험 안내를 먼저 확인하세요")
    row = session_row(s)
    if row.state == "needs_user" and kind != "login":
        raise CafeError(409, "cafe_needs_user", "브라우저에서 인증·차단 화면을 직접 확인하세요")
    active = s.scalar(select(CafeJob).where(CafeJob.state.in_(("queued", "running"))).limit(1))
    if active:
        raise CafeError(409, "cafe_job_active", "진행 중인 카페 작업이 끝난 뒤 다시 실행하세요")
    job = CafeJob(id=new_id("cfj"), kind=kind, state="queued", created_at=now, input=input or {}, result={})
    s.add(job)
    try:
        s.flush()
    except IntegrityError:
        raise CafeError(409, "cafe_job_active", "진행 중인 카페 작업이 끝난 뒤 다시 실행하세요") from None
    return job


def claim_next_job(s: Session, now: datetime) -> CafeJob | None:
    if not consent_accepted(s):
        return None
    row = s.scalar(select(CafeJob).where(CafeJob.state == "queued").order_by(CafeJob.created_at).limit(1))
    if row:
        row.state, row.started_at = "running", now
    return row


def finish_job(s: Session, job_id: str, state: str, now: datetime, *, error_code: str | None = None, error_message: str | None = None, result: dict | None = None) -> None:
    if state not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("invalid terminal cafe job state")
    row = s.get(CafeJob, job_id)
    if row is None:
        return
    row.state, row.finished_at = state, now
    row.error_code, row.error_message, row.result = error_code, error_message, result or {}
    emit(s, "cafe.job_changed", {"job_id": row.id, "state": state})


def recover_interrupted_jobs(s: Session, now: datetime) -> None:
    from ..models import CafePostAttempt
    for attempt in s.scalars(select(CafePostAttempt).where(CafePostAttempt.status == 'pending')):
        attempt.status, attempt.error_code = 'failed', 'cafe_submission_uncertain'
        attempt.error_message = '중단된 게시 결과를 직접 대조하세요. 자동 재시도하지 않습니다'
    for row in s.scalars(select(CafeJob).where(CafeJob.state.in_(("queued", "running")))):
        finish_job(s, row.id, "failed", now, error_code="cafe_interrupted", error_message="앱 종료 또는 복원으로 중단되었습니다. 자동 재시도하지 않습니다")


def list_jobs(s: Session) -> S.CafeJobList:
    return S.CafeJobList(items=[job_out(row) for row in s.scalars(select(CafeJob).order_by(CafeJob.created_at.desc()).limit(100))])


def entry_out(s: Session, row: Cafe) -> S.CafeEntry:
    values = {key: getattr(row, key) for key in S.CafeEntry.model_fields if key != "boards"}
    values["boards"] = [S.CafeBoardOut.model_validate({key: getattr(board, key) for key in S.CafeBoardOut.model_fields})
        for board in s.scalars(select(CafeBoard).where(CafeBoard.cafe_id == row.id).order_by(CafeBoard.name))]
    return S.CafeEntry(**values)


def list_cafes(s: Session) -> S.CafeList:
    return S.CafeList(items=[entry_out(s, row) for row in s.scalars(select(Cafe).order_by(Cafe.first_seen_at.desc()))])


def add_cafe(s: Session, url: str, now: datetime) -> Cafe:
    url = canonical_cafe_url(url)
    row = s.scalar(select(Cafe).where(Cafe.url == url))
    if row is None:
        row = Cafe(id=new_id("caf"), url=url, name=url.rsplit("/", 1)[-1], status="candidate", first_seen_at=now)
        s.add(row)
        s.flush()
    return row


def update_cafe(s: Session, id_: str, status: str, now: datetime) -> S.CafeEntry:
    row = s.get(Cafe, id_)
    if row is None:
        raise CafeError(404, "cafe_not_found", "카페를 찾을 수 없습니다")
    if status not in {"candidate", "excluded", "approved"}:
        raise CafeError(409, "cafe_invalid_transition", "가입 여부와 규칙 판정은 조사 결과로만 변경됩니다")
    if status == "approved":
        allowed = s.scalar(select(CafeBoard).where(CafeBoard.cafe_id == id_, CafeBoard.post_allowed.is_(True)).limit(1))
        if row.joined is not True or row.status not in {"eligible", "approved"} or not allowed:
            raise CafeError(409, "cafe_rule_unverified", "가입과 명시적인 게시 허용 근거를 먼저 확인해야 합니다")
        row.approved_at = now
    else:
        row.approved_at = None
        for board in s.scalars(select(CafeBoard).where(CafeBoard.cafe_id == id_)):
            board.approved = False
    row.status = status
    return entry_out(s, row)


def approve_board(s: Session, id_: str, approved: bool) -> S.CafeBoardOut:
    row = s.get(CafeBoard, id_)
    if row is None:
        raise CafeError(404, "cafe_board_not_found", "게시판을 찾을 수 없습니다")
    cafe = s.get(Cafe, row.cafe_id)
    if approved and (not row.post_allowed or cafe is None or cafe.status != "approved"):
        raise CafeError(409, "cafe_rule_unverified", "게시 허용 근거와 카페 승인이 필요합니다")
    if approved:
        for other in s.scalars(select(CafeBoard).where(CafeBoard.cafe_id == row.cafe_id)):
            other.approved = False
    row.approved = approved
    return S.CafeBoardOut.model_validate({key: getattr(row, key) for key in S.CafeBoardOut.model_fields})


def save_discovery(s: Session, items: list[dict], now: datetime) -> None:
    for item in items:
        row = add_cafe(s, item["url"], now)
        for key in ("name", "region", "category", "member_count", "post_count", "joined", "rule_evidence"):
            if key in item:
                setattr(row, key, item[key])
        row.last_checked_at = now
        # A name or absent prohibition is never permission to publish.
        inspected_status = item.get("status", "candidate")
        if inspected_status == "excluded":
            row.status, row.approved_at = "excluded", None
        elif row.status not in {"excluded", "approved"}:
            row.status = inspected_status
        row.status_reason = item.get("status_reason", "게시 허용 근거를 확인해야 합니다")
        for value in item.get("boards", []):
            url = canonical_cafe_url(value["url"], board=True)
            if url.split("/boards/")[0] != row.url:
                continue
            board = s.scalar(select(CafeBoard).where(CafeBoard.url == url))
            if board is None:
                board = CafeBoard(id=new_id("cfb"), cafe_id=row.id, url=url, name=value["name"], post_allowed=False, approved=False)
                s.add(board)
            board.name, board.kind, board.evidence = value["name"], value.get("kind", "other"), value.get("evidence")
            from ..cafe.parsing import classify_board
            decision = classify_board(board.name, board.evidence or "")
            board.post_allowed = decision["post_allowed"]
            if not board.post_allowed or row.status != "approved":
                board.approved = False
        if row.status == "approved" and (row.joined is not True or not any(b.post_allowed for b in s.scalars(select(CafeBoard).where(CafeBoard.cafe_id == row.id)))):
            row.status, row.approved_at = "candidate", None
        if row.status == "eligible" and get_settings(s)["auto_approve"]:
            row.status, row.approved_at = "approved", now
    s.flush()
