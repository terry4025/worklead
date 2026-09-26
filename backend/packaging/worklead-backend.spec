# -*- mode: python ; coding: utf-8 -*-
# 백엔드 sidecar 단일 실행 파일. Tauri 가 binaries/worklead-backend-<target-triple>(.exe) 로 동봉한다.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - PyInstaller 가 제공
PKG = ROOT / "worklead"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(PKG / "migrations"), "worklead/migrations"),
        (str(PKG / "sources" / "daangn" / "profile.json"), "worklead/sources/daangn"),
        (str(PKG / "sources" / "albamon" / "profile.json"), "worklead/sources/albamon"),
    ],
    hiddenimports=collect_submodules("worklead") + collect_submodules("uvicorn") + collect_submodules("alembic"),
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="worklead-backend",
    debug=False,
    strip=False,
    upx=False,
    console=True,  # stdout 준비 줄이 필요. Tauri 가 창 없이(CREATE_NO_WINDOW) 실행한다
    runtime_tmpdir=None,
)
