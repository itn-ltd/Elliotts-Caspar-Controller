# -*- mode: python ; coding: utf-8 -*-
import os
import sys

try:
    from PyInstaller.building.build_main import Analysis
    from PyInstaller.building.api import EXE, PYZ, COLLECT
except ImportError:
    from PyInstaller.building.api import Analysis, EXE, PYZ, COLLECT

from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, os.path.abspath('.'))
try:
    from caspar_controller import __version__
    VERSION = __version__
except Exception:
    VERSION = "0.1.0"

datas = []
for folder in ('caspar_controller/static', 'caspar_controller/caspar/test_patterns'):
    datas.append((folder, folder))
if os.path.exists('README.md'):
    datas.append(('README.md', '.'))

a = Analysis(
    ['caspar_controller/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # uvicorn and pystray pick their implementations at runtime.
    hiddenimports=collect_submodules('uvicorn') + ['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='ElliottsCasparController',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='caspar_controller/static/esc_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ElliottsCasparController',
)
