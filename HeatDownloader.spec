# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for HeatDownloader one-file Windows build.

from __future__ import annotations

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

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

# Embed Windows VERSIONINFO so the binary looks like a normal app, not a packer stub.
_version_match = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    Path("heat_downloader/__init__.py").read_text(encoding="utf-8"),
)
_app_version = _version_match.group(1) if _version_match else "0.0.0"
_parts = [int(p) for p in _app_version.split(".") if p.isdigit()]
while len(_parts) < 4:
    _parts.append(0)
_filevers = tuple(_parts[:4])
_version_info = Path("build") / "file_version_info.txt"
_version_info.parent.mkdir(parents=True, exist_ok=True)
_version_info.write_text(
    f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_filevers!r},
    prodvers={_filevers!r},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'macu7'),
            StringStruct('FileDescription', 'Heat Downloader'),
            StringStruct('FileVersion', {_app_version!r}),
            StringStruct('InternalName', 'HeatDownloader'),
            StringStruct('LegalCopyright', 'Copyright (c) macu7'),
            StringStruct('OriginalFilename', 'HeatDownloader.exe'),
            StringStruct('ProductName', 'Heat Downloader'),
            StringStruct('ProductVersion', {_app_version!r}),
          ],
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""",
    encoding="utf-8",
)

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
    # UPX packing is a common AV false-positive trigger; keep the binary unpacked.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(_version_info),
)
