# -*- mode: python ; coding: utf-8 -*-
# Windows build of EQ Autopilot (run on a Windows host — PyInstaller can't cross-compile).
# Built in CI by .github/workflows/build-windows.yml (windows-latest). Output: dist/EQ Autopilot/.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = ['autopilot_crypto', 'yaml', 'requests', 'urllib3', 'tzdata', 'tkinter.simpledialog',
                 'keyring', 'keyring.backends.Windows', 'win32ctypes', 'win32ctypes.pywin32', 'nest_asyncio']
hiddenimports += collect_submodules('eqexec')
hiddenimports += collect_submodules('keyring')
hiddenimports += collect_submodules('cryptography')   # v2 AES-GCM 피드(2026-09-23) - 빠지면 앱이 aead=False를 광고해 v1을 받는다
hiddenimports += collect_submodules('ib_insync')   # IBKR 어댑터(지연 import) — 번들에 포함
hiddenimports += collect_submodules('eventkit')

a = Analysis(
    ['eqgui.py'],
    pathex=[],
    binaries=[],
    datas=[('eqlogo.png', '.'), ('eqlogo256.png', '.'), ('eqicon.ico', '.'), ('nt8_addon/EQAutopilotBridge.cs', 'nt8_addon'), ('nt8_addon/README.md', 'nt8_addon')] + collect_data_files('tzdata'),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EQ Autopilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX not on the runner; can flag AV false-positives on Windows
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['eqicon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EQ Autopilot',
)
