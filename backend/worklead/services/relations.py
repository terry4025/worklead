"""Candidate relationships stay separate from both leads and their user data."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..engine.events import emit
from ..models import DuplicateLink, Lead, LeadActivity, SourceRecord, new_id


class RelationMissing(Exception):
    pass


class RelationConflict(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    primary: DuplicateLink
    confirmed: bool
    dismissed: bool
    revision: int


def between(s: Session, a: str, b: str) -> list[DuplicateLink]:
    records_a = select(SourceRecord.id).where(SourceRecord.lead_id == a)
    records_b = select(SourceRecord.id).where(SourceRecord.lead_id == b)
    return list(s.scalars(select(DuplicateLink).where(or_(
        and_(DuplicateLink.lead_id == a, DuplicateLink.source_record_id.in_(records_b)),
        and_(DuplicateLink.lead_id == b, DuplicateLink.source_record_id.in_(records_a)),
    )).order_by(DuplicateLink.created_at, DuplicateLink.id)))


def decision(links: list[DuplicateLink]) -> Decision:
    # Older DBs can contain more than one directed row for the same pair.
    # One stable ID works from either lead. The latest user decision wins;
    # a dismissal wins an ambiguous tie rather than silently reconnecting it.
    primary = min(links, key=lambda link: (link.created_at, link.id))
    latest = max(links, key=lambda link: (link.revision, link.dismissed, link.confirmed, link.created_at, link.id))
    return Decision(primary, latest.confirmed and not latest.dismissed, latest.dismissed, latest.revision)


def update_decision(s: Session, lead: Lead, link_id: str, action: str, expected_revision: int) -> Lead:
    link = s.get(DuplicateLink, link_id)
    record = s.get(SourceRecord, link.source_record_id) if link else None
    if link is None or record is None or not record.lead_id or record.lead_id == link.lead_id:
        raise RelationMissing()
    endpoints = (link.lead_id, record.lead_id)
    if lead.id not in endpoints:
        raise RelationMissing()
    other_id = endpoints[1] if lead.id == endpoints[0] else endpoints[0]
    other = s.get(Lead, other_id)
    if other is None:
        raise RelationMissing()
    links = between(s, lead.id, other_id)
    current = decision(links)
    confirmed, dismissed = action == "confirm", action == "dismiss"
    if all(row.confirmed == confirmed and row.dismissed == dismissed for row in links):
        return other  # Retrying a completed action has no extra side effects.
    if expected_revision != current.revision:
        raise RelationConflict()
    next_revision = current.revision + 1
    claimed = s.execute(update(DuplicateLink).where(
        DuplicateLink.id == current.primary.id,
        DuplicateLink.revision == current.primary.revision,
    ).values(confirmed=confirmed, dismissed=dismissed, revision=next_revision))
    if claimed.rowcount != 1:
        raise RelationConflict()
    s.execute(update(DuplicateLink).where(DuplicateLink.id.in_([row.id for row in links])).values(
        confirmed=confirmed, dismissed=dismissed, revision=next_revision))
    now = utcnow()
    text = "관련 글 관계를 사용자 확인" if confirmed else "관련 글 연결을 사용자 해제"
    for endpoint in (lead, other):
        s.add(LeadActivity(id=new_id("act"), lead_id=endpoint.id, at=now, kind="relation", text=text))
        emit(s, "lead.updated", {"lead_id": endpoint.id, "changes": ["relations"]})
    s.flush()
    return other
