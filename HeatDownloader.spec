# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for HeatDownloader one-file Windows build.

from PyInstaller.utils.hooks import collect_all

from pathlib import Path

datas = []
binaries = []
hiddenimports = []

for pkg in ("curl_cffi", "certifi", "bs4", "soupsieve"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

# Bundle heat_probes.json so first run next to the exe can bootstrap the cache.
seed_path = Path("heat_probes.json")
if seed_path.exists():
    datas.append((str(seed_path.resolve()), "."))

a = Analysis(
    ["heat_downloader.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "heat_downloader",
        "heat_downloader.cli",
        "heat_downloader.artifacts",
        "heat_downloader.patreon",
        "heat_downloader.steamdb",
        "heat_downloader.steam",
        "heat_downloader.models",
        "heat_downloader.http",
        "heat_downloader.download",
        "heat_downloader.extract",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HeatDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
