"""Collection must survive cleanup of one-file certificate resources."""
import ssl

import certifi
import httpx

from tests.conftest import make_ctx
from worklead.engine.http import Fetcher, _TLS_CONTEXT


def test_new_collection_client_survives_missing_certificate_file(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "removed" / "cacert.pem"))
    assert _TLS_CONTEXT.verify_mode == ssl.CERT_REQUIRED
    assert _TLS_CONTEXT.check_hostname
    assert _TLS_CONTEXT.cert_store_stats()["x509_ca"] > 0
    fetcher = Fetcher(ctx.db, allowed_hosts={"example.com"}, user_agent="test",
                      daily_budget=10, min_interval_s=0,
                      transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))
    try:
        assert fetcher.client.get("https://example.com").text == "ok"
    finally:
        fetcher.close()
