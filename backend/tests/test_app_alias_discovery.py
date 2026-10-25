from worklead.sources.daangn import create


def test_app_keyword_includes_spelling_aliases_without_ignoring_query_scope():
    adapter = create()
    for title in ['어플리케이션 개발해주실 분', '애플리케이션 제작해주실 분', '어플 개발 의뢰']:
        assert adapter.saved_candidate_kind(title, '["앱"]') == 'core'
        assert adapter.saved_candidate_kind(title, '["엑셀"]') is None
    assert adapter.saved_candidate_kind('어플리케이션 설치하고 포인트 받기', '["앱"]') != 'core'
