"""코드에 등록된 어댑터 목록. 원격 플러그인을 내려받아 실행하지 않는다.

새 사이트 추가: sources/<site>/ 에 어댑터 + 조사 프로필 + 테스트 픽스처를 만들고 여기에 등록한다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Source, SourcePolicy
from . import daangn
from .manual import ManualAdapter


def default_adapters() -> dict[str, object]:
    adapters: list[object] = [daangn.create(), ManualAdapter()]
    return {a.source_id: a for a in adapters}  # type: ignore[attr-defined]


def sync_sources(s: Session, adapters: dict[str, object]) -> None:
    """등록된 어댑터를 sources 표에 반영한다. 사이트 소스의 정책 기본값은 permission_pending."""
    for sid, a in adapters.items():
        row = s.get(Source, sid)
        if row is None:
            row = Source(
                id=sid,
                name=getattr(a, "name"),
                kind=getattr(a, "kind"),
                adapter_version=getattr(a, "adapter_version"),
                scope_note=getattr(a, "scope_note"),
                auto_collect_enabled=False,
                interval_minutes=getattr(a, "default_interval_minutes"),
                health_status="unknown" if getattr(a, "kind") == "site" else "ok",
                created_at=utcnow(),
            )
            s.add(row)
        else:
            row.name = getattr(a, "name")
            row.adapter_version = getattr(a, "adapter_version")
            row.scope_note = getattr(a, "scope_note")
        if s.get(SourcePolicy, sid) is None:
            s.add(
                SourcePolicy(
                    source_id=sid,
                    status="permission_pending" if getattr(a, "kind") == "site" else "not_required",
                    reviewed_by="system",
                )
            )
    s.flush()
