"""contracts/fixtures 가 스키마(Pydantic 계약)와 일치하는지 재검증한다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worklead.api import schemas as S

FIX = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"

CASES = [
    ("bootstrap.json", S.BootstrapOut),
    ("health.json", S.HealthOut),
    ("settings.json", S.SettingsOut),
    ("metrics.json", S.MetricsOut),
    ("sources.first_run.json", S.SourceList),
    ("queue-counts.json", S.QueueCounts),
    ("leads.all.json", S.LeadPage),
    ("leads.empty.json", S.LeadPage),
]


@pytest.mark.parametrize(("name", "model"), CASES)
def test_fixture_matches_schema(name: str, model: type) -> None:
    model.model_validate(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_details_and_scenarios() -> None:
    details = sorted((FIX / "lead-details").glob("*.json"))
    assert details
    for p in details:
        S.LeadDetail.model_validate(json.loads(p.read_text(encoding="utf-8")))
    for sc in (FIX / "scenarios").iterdir():
        S.SourceList.model_validate(json.loads((sc / "sources.json").read_text(encoding="utf-8")))
        S.RunPage.model_validate(json.loads((sc / "runs.json").read_text(encoding="utf-8")))
        S.LeadPage.model_validate(json.loads((sc / "leads.json").read_text(encoding="utf-8")))
    for p in (FIX / "errors").glob("*.json"):
        S.ErrorResponse.model_validate(json.loads(p.read_text(encoding="utf-8")))
