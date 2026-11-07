"""Reuse only fixed app-owned browser profiles, with one live owner at a time."""
from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

LOCK_NAME = ".worklead-profile.lock"
_guard = threading.RLock()
_owners: set[str] = set()


def _absolute(path: Path) -> Path:
    # abspath normalizes dot segments without following a symlink or junction.
    return Path(os.path.abspath(os.fspath(path)))


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _plain_directories(path: Path, *, required=False) -> bool:
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if required and current == path:
                return False
            continue
        except OSError:
            return False
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def select_managed_profile(data_dir: Path) -> Path:
    """Choose an existing Worklead session without reading/copying its cookies."""
    root = _absolute(data_dir)
    if not _plain_directories(root):
        raise RuntimeError("jobs_profile_unavailable")
    legacy = root / "jobs-browser-profile"
    if _plain_directories(legacy, required=True) and _plain_directories(legacy / "Default", required=True):
        return legacy
    managed = root / "jobs-auto-browser-profile"
    if not _plain_directories(managed):
        raise RuntimeError("jobs_profile_unavailable")
    return managed


class ProfileLease:
    """Nonblocking process/OS lease; the fixed lock file is never deleted."""

    def __init__(self, profile: Path):
        self.profile = _absolute(profile)
        self._key = os.path.normcase(str(self.profile))
        self._fd: int | None = None
        self._state_lock = threading.RLock()

    def acquire(self) -> bool:
        with self._state_lock:
            if self._fd is not None:
                return True
            with _guard:
                if self._key in _owners:
                    return False
                _owners.add(self._key)
            fd = None
            locked = False
            try:
                if not _plain_directories(self.profile):
                    return False
                self.profile.mkdir(parents=True, exist_ok=True)
                if not _plain_directories(self.profile, required=True):
                    return False
                lock = self.profile / LOCK_NAME
                try:
                    info = lock.lstat()
                except FileNotFoundError:
                    info = None
                if info is not None and (_linked(info) or not stat.S_ISREG(info.st_mode)):
                    return False
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(lock, flags, 0o600)
                info = os.fstat(fd)
                if _linked(info) or not stat.S_ISREG(info.st_mode):
                    return False
                if info.st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                locked = True
                return True
            except (OSError, ValueError):
                return False
            finally:
                if not locked:
                    if fd is not None:
                        os.close(fd)
                    with _guard:
                        _owners.discard(self._key)

    def release(self) -> None:
        with self._state_lock:
            fd, self._fd = self._fd, None
            if fd is None:
                return
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                try:
                    os.close(fd)
                finally:
                    with _guard:
                        _owners.discard(self._key)
