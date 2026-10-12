from datetime import timedelta

from tests.conftest import make_ctx
from tests.test_source_fields import NOW, sample
from worklead.analysis.provider import AIOutput
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import AnalysisResult, Lead, SourceRecord
from worklead.services.settings import put_setting


class RecordingProvider:
    engine = "fixture"
    model = "model-one"

    def __init__(self):
        self.inputs = []

    def available(self):
        return True

    def analyze(self, data):
        self.inputs.append(data)
        return AIOutput(summary=f"분석 {self.model}")


def test_changing_provider_model_invalidates_cache_and_records_the_new_model(tmp_path):
    ctx = make_ctx(tmp_path)
    provider = RecordingProvider()
    try:
        with ctx.db.session() as s:
            put_setting(s, "ai", {"enabled": True, "external_transfer_consent": True})
            first = ingest(s, "daangn-alba", sample(), parser_version="test", now=NOW, provider=provider)
        with ctx.db.session() as s:
            lead, rec = s.get(Lead, first.lead_id), s.get(SourceRecord, first.record_id)
            apply_analysis(s, lead, rec, now=NOW, provider=provider)
            assert len(provider.inputs) == 1
            old_id = lead.analysis_id
            provider.model = "model-two"
            apply_analysis(s, lead, rec, now=NOW + timedelta(minutes=1), provider=provider)
            assert len(provider.inputs) == 2
            assert lead.analysis_id != old_id
            assert s.get(AnalysisResult, lead.analysis_id).model == "model-two"
            assert s.get(AnalysisResult, lead.analysis_id).payload["summary"] == "분석 model-two"
    finally:
        ctx.db.dispose()


def test_all_transmitted_source_text_is_redacted_without_changing_local_records(tmp_path):
    ctx = make_ctx(tmp_path)
    provider = RecordingProvider()
    try:
        post = sample()
        post.title = "홈페이지 제작 의뢰 fixture@example.invalid 010-1234-5678"
        post.body += " 연락처 fixture@example.invalid 010-1234-5678"
        with ctx.db.session() as s:
            put_setting(s, "ai", {"enabled": True, "external_transfer_consent": True})
            result = ingest(s, "daangn-alba", post, parser_version="test", now=NOW, provider=provider)
            assert s.get(SourceRecord, result.record_id).title == post.title
            assert s.get(SourceRecord, result.record_id).body_text == post.body
        transmitted = provider.inputs[0]
        for text in (transmitted.title, transmitted.body, transmitted.rule_summary or ""):
            assert "fixture@example.invalid" not in text
            assert "010-1234-5678" not in text
        assert "[이메일]" in transmitted.title and "[전화번호]" in transmitted.body
    finally:
        ctx.db.dispose()
