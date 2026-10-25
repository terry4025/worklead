"""Prepare reviewed extension files for manual installation; never install it."""
import os
import sys
from pathlib import Path

FILES = ('manifest.json', 'protocol.js', 'content.js', 'worker.js', 'popup.html', 'popup.css', 'popup.js',
         'auto-protocol.js', 'auto-content.js', 'auto-worker.js')


def prepare_extension_directory(data_dir: Path) -> str:
    from .applications import ApplicationError
    source = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[3])) / 'extensions/worklead-messages'
    target = data_dir / 'chrome-message-extension'
    try:
        # Reject links before writing. Only the named application-owned files are replaced.
        if target.is_symlink():
            raise OSError('linked destination')
        target.mkdir(parents=True, exist_ok=True)
        if os.path.normcase(str(target.resolve().parent)) != os.path.normcase(str(data_dir.resolve())):
            raise OSError('unexpected destination')
        values = {name: (source / name).read_bytes() for name in FILES}
        for name in FILES:
            if (target / name).is_symlink():
                raise OSError('linked file')
        for name, value in values.items():
            (target / name).write_bytes(value)
        return str(target.resolve())
    except OSError:
        raise ApplicationError(503, 'chrome_extension_unavailable', '크롬 연결 확장 파일을 준비하지 못했습니다. 설치본과 데이터 폴더를 확인하세요') from None
