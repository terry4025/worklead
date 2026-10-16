import json
from pathlib import Path

import pytest

from worklead.jobs.chrome_extension import prepare_extension_directory, FILES
from worklead.jobs.chrome_bridge import EXTENSION_ID


def test_extension_preparation_and_scope(tmp_path):
    import base64, hashlib
    path = Path(prepare_extension_directory(tmp_path))
    assert path == (tmp_path / 'chrome-message-extension').resolve()
    assert sorted(item.name for item in path.iterdir()) == sorted(FILES)
    assert prepare_extension_directory(tmp_path) == str(path)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    assert ''.join(chr(97 + int(ch, 16)) for ch in digest) == EXTENSION_ID
    assert manifest['permissions'] == ['activeTab', 'scripting', 'storage']
    assert manifest['optional_host_permissions'] == ['http://127.0.0.1/*']
    assert not manifest.get('host_permissions')
    assert not manifest.get('content_scripts')
    assert not manifest.get('externally_connectable')
    assert 'cookies' not in manifest['permissions'] and 'debugger' not in manifest['permissions']


def test_does_not_overwrite_linked_extension_file(tmp_path):
    from worklead.jobs.applications import ApplicationError
    target = Path(prepare_extension_directory(tmp_path))
    outside = tmp_path / 'preserved.txt'
    outside.write_text('preserved', encoding='utf-8')
    (target / 'worker.js').unlink()
    try:
        (target / 'worker.js').symlink_to(outside)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    with pytest.raises(ApplicationError) as error:
        prepare_extension_directory(tmp_path)
    assert error.value.code == 'chrome_extension_unavailable'
    assert outside.read_text(encoding='utf-8') == 'preserved'
