"""TanStack Start stream decoding, shaped like jobs.daangn.com on 2026-11-07."""
import json
import re

import pytest

from worklead.sources.daangn.serialized import SerializedError, _Reader, relay_records

_KEY = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def js(value) -> str:
    """Render test data the way seroval does (literal subset only)."""
    if value is True:
        return "!0"
    if value is False:
        return "!1"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(js(v) for v in value) + "]"
    return "{" + ",".join((k if _KEY.fullmatch(k) else json.dumps(k, ensure_ascii=False)) + ":" + js(v)
                          for k, v in value.items()) + "}"


def tsr_script(store: dict) -> str:
    """One router stream part carrying `store` as dehydrated relay records."""
    return ('<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={manifest:$R[1]={routes:$R[2]={}},'
            'matches:$R[3]=[$R[4]={i:"__root__",s:"success",l:$R[5]={},ssr:!0}],'
            f'dehydratedData:$R[6]={{relayRecords:$R[7]={js(store)}}}}})($R["tsr"])</script>')


def as_tsr(markup: str) -> str:
    """Replace a legacy window.__RELAY_STORE__ script with the current stream form."""
    from worklead.sources.daangn.adapter import _Document
    store = _Document(markup).store()
    legacy = re.compile(r"<script[^>]*>\s*window\.__RELAY_STORE__.*?</script>", re.S)
    assert legacy.search(markup), "fixture has no legacy store"
    return legacy.sub(lambda _: tsr_script(store), markup, count=1)


def wrap(body: str) -> str:
    return '<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={dehydratedData:{' + body + '}})($R["tsr"])</script>'


def test_literal_subset():
    text = '$R[0]={a:!0,"b c":[1,-2.5,null,void 0],d:"x",e:$R[1]={f:!1},g:$R[1]}'
    assert _Reader(text, 0, {}).value() == {"a": True, "b c": [1, -2.5, None, None], "d": "x",
                                            "e": {"f": False}, "g": {"f": False}}


def test_string_escapes_and_surrogate_pairs():
    assert _Reader('"a\\nb\\u00e9\\ud83d\\ude00\\x41\\u{1F600}"', 0, {}).value() == "a\nbé😀A😀"


@pytest.mark.parametrize("bad", ['alert(1)', 'function(){}', '"unterminated', '$R[9]', '{a:1'])
def test_rejects_code_truncation_and_unknown_slots(bad):
    with pytest.raises(SerializedError):
        relay_records(wrap(f"relayRecords:$R[1]={bad}"))


def test_back_reference_defined_before_relay_records():
    script = ('<script data-tsr-stream-part="">$_TSR.router=($R=>$R[0]={matches:$R[1]=[$R[2]={l:$R[3]={workDays:$R[4]=["MON"]}}],'
              'dehydratedData:$R[5]={relayRecords:$R[6]={"client:root":$R[7]={__id:"client:root"},job:$R[8]={workDays:$R[4]}}}})($R["tsr"])</script>')
    assert relay_records(script)["job"]["workDays"] == ["MON"]


def test_skips_non_router_stream_parts():
    store = {"client:root": {"__id": "client:root"}, "job": {"title": "홈페이지 제작"}}
    markup = ('<script data-tsr-stream-part="">(self.$R=self.$R||{})["tsr"]=[];</script>'
              + tsr_script(store) + '<script data-tsr-stream-part="">$_TSR.e();</script>')
    assert relay_records(markup)["job"]["title"] == "홈페이지 제작"


def test_missing_stream_or_root_is_an_error():
    with pytest.raises(SerializedError):
        relay_records("<html><body><h1>새 화면</h1></body></html>")
    with pytest.raises(SerializedError):
        relay_records(tsr_script({"job": {"title": "x"}}))


def test_out_of_range_code_point_is_a_serialized_error():
    with pytest.raises(SerializedError):
        relay_records(wrap('relayRecords:$R[1]={"client:root":{},x:"\\u{FFFFFFFFFFFFFFFFFFFF}"}'))
