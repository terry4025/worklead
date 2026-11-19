from datetime import UTC, datetime, timedelta

import pytest

from worklead.analysis.service import analyze, WEIGHTS
from worklead.analysis.types import DateInfo, PostInput, Profile

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def check(title, body, *, age=0, profile=None):
    return analyze(PostInput(title=title, body=body + '\n건당 30만원', observed_at=NOW,
        source_status='open', last_checked_at=NOW, first_seen_at=NOW-timedelta(days=age),
        published=DateInfo(at=NOW-timedelta(days=age), precision='exact')),
        profile or Profile(), NOW)


def test_explicit_delivery_is_not_drowned_by_missing_remote_or_empty_profile():
    a = check('홈페이지 만들어주실 분', '홈페이지 제작 의뢰합니다.', age=14)
    assert a.total >= 50
    assert a.recommendation == 'needs_review'
    assert a.judgements['work_mode'].value == 'unknown'
    assert next(f for f in a.factors if f.key == 'remote_fit').score is None
    assert sum(mx for _, mx in WEIGHTS.values()) == 100


def test_ongoing_delivery_has_same_buyer_strength_as_one_off():
    one = check('홈페이지 제작 의뢰', '홈페이지 제작 의뢰합니다.')
    ongoing = check('홈페이지 제작 의뢰', '홈페이지 제작 의뢰합니다. 유지보수도 의뢰합니다.')
    buyer = lambda a: next(f.score for f in a.factors if f.key == 'buyer_intent')
    assert buyer(one) == buyer(ongoing)
    assert ongoing.judgements['demand_intent'].value == 'buyer_ongoing'


@pytest.mark.parametrize('title,body', [
    ('기존 홈페이지 버그 고쳐주실 분', '홈페이지 버그 고쳐주세요.'),
    ('워드프레스 복구 부탁드립니다', '워드프레스 접속 오류 복구 부탁드립니다.'),
    ('프로그램 버그 고쳐주실 분', '프로그램 버그 고쳐주세요.'),
    ('어플리케이션 제작해주실 분', '어플리케이션 개발 의뢰합니다.'),
    ('애플리케이션 제작해주실 분', '애플리케이션 개발 의뢰합니다.'),
    ('API 연동 의뢰', 'API 연동 의뢰합니다.'),
])
def test_repairs_deployment_and_application_delivery_are_development(title, body):
    a = check(title, body)
    assert a.dev_relevant
    assert a.judgements['demand_intent'].value.startswith('buyer')
    assert a.recommendation == 'needs_review'
    assert a.total >= 50


@pytest.mark.parametrize('title,body', [
    ('홈페이지 제작 직원 채용', '홈페이지 제작 직원 모집. 정규직 채용합니다.'),
    ('홈페이지 만들어드립니다', '홈페이지 만들어드립니다.'),
    ('프로그램 복구 방법 과외', '프로그램 복구 방법을 가르쳐 주세요.'),
    ('홈페이지제작하는거 알려주실분', '홈페이지 제작을 도와주실 분을 찾고 있어요. 코딩을 활용해 만들고 싶고 기초 설정부터 차근차근 배우고 싶어요.'),
    ('홈페이지 복구 의뢰는 아닙니다', '홈페이지 복구 의뢰는 아닙니다. 방법만 알려주세요.'),
    ('홈페이지 참고해서 에어컨 고쳐주세요', '홈페이지에 있는 에어컨 고쳐주세요.'),
])
def test_fit_boost_does_not_make_employment_sellers_advice_or_physical_jobs_eligible(title, body):
    assert check(title, body).recommendation == 'excluded'


def test_explicitly_unsupported_service_is_not_given_supported_fit():
    a = check('앱 제작해주실 분', '앱 개발 의뢰합니다.', profile=Profile(services=['vba']))
    fit = next(f for f in a.factors if f.key == 'work_fit')
    assert fit.score == 16 and '목록에 없음' in fit.reason
