"""Message pairing has a purpose-specific sealed Windows vault target."""
import json, os, secrets, sys, uuid
import pytest
from tests.test_auto_pair_credentials import Api
from worklead.services.message_pair_credentials import MessagePairCredentialStore
from worklead.services.auto_pair_credentials import AutoPairCredentialStore
from worklead.services.credentials import CredentialError


def grant():
    return {"version":1,"token":secrets.token_urlsafe(32),"desired":True,"account":"a"*64,"capture":True,"paused":None}


def test_message_scope_is_distinct_and_buffers_are_wiped(tmp_path):
    api=Api();store=MessagePairCredentialStore(api=api);value=grant()
    assert store.read(tmp_path) is None
    store.write(tmp_path,value)
    assert store.read(tmp_path)==value
    assert store._target(tmp_path).startswith('Worklead/jobs/message-pair/v1/')
    assert store._target(tmp_path)!=AutoPairCredentialStore(api=api)._target(tmp_path)
    assert store.read(tmp_path/'different') is None
    assert all(not any(x) for x in api.buffers)
    assert list(tmp_path.iterdir())==[]
    assert store.delete(tmp_path) and not store.delete(tmp_path)


@pytest.mark.parametrize('patch',[{'capture':1},{'desired':0},{'account':'bad'},{'version':True},{'token':'short'},{'message_body':'never accepted'},{'paused':'unknown'}])
def test_message_grant_rejects_corruption_and_body_fields(tmp_path,patch):
    api=Api();store=MessagePairCredentialStore(api=api);bad={**grant(),**patch}
    with pytest.raises(CredentialError,match='^credential_corrupt$'):store.write(tmp_path,bad)
    api.values[store._target(tmp_path)]=json.dumps(bad).encode()
    with pytest.raises(CredentialError,match='^credential_corrupt$'):store.read(tmp_path)
    assert all(not any(x) for x in api.buffers)


@pytest.mark.skipif(sys.platform!='win32' or os.environ.get('WORKLEAD_TEST_CREDENTIAL_OS')!='1',reason='opt-in owned Windows message credential probe')
def test_actual_windows_message_vault_owned_uuid_roundtrip(tmp_path):
    scope=tmp_path/('message-pair-probe-'+uuid.uuid4().hex);scope.mkdir()
    store=MessagePairCredentialStore();value=grant()
    assert store.read(scope) is None
    try:
        store.write(scope,value);assert store.read(scope)==value
        store.write(scope,{**value,'desired':False});assert store.read(scope)['desired'] is False
        assert list(scope.iterdir())==[]
    finally:store.delete(scope)
    assert store.read(scope) is None
