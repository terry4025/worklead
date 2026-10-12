from tests.conftest import client_for
from tests.test_engine import setup
from worklead.engine import runs


def test_active_and_discovery_filters_survive_many_individual_runs(tmp_path):
    ctx, _ = setup(tmp_path)
    with ctx.db.session() as s:
        discovery, _ = runs.enqueue(s, 'discovery', source_id='fixture-site')
        discovery_id = discovery.id
        discovery.state = 'partial'
        pending, _ = runs.enqueue(s, 'recheck_recent', source_id='fixture-site')
        pending_id = pending.id
        for _ in range(30):
            other, _ = runs.enqueue(s, 'reanalyze_all')
            other.state = 'succeeded'
    c = client_for(ctx)
    ordinary = c.get('/v1/runs').json()['items']
    assert discovery_id not in {x['id'] for x in ordinary}
    assert pending_id not in {x['id'] for x in ordinary}
    active = c.get('/v1/runs', params=[('state', 'queued'), ('state', 'running'), ('state', 'paused')]).json()['items']
    assert [x['id'] for x in active] == [pending_id]
    latest = c.get('/v1/runs', params={'kind': 'discovery', 'state': 'partial', 'limit': 1}).json()['items']
    assert [x['id'] for x in latest] == [discovery_id]
    assert c.get('/v1/runs', params={'state': 'invalid'}).status_code == 422
