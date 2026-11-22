# 테스트용 앱(EQ Autopilot Test, 2026-11-13 대표 "앱을 이름 다르게 빌드"): 번들에 eq_flavor.txt='test' - 늘 데모, 설정 폴더 EQAutopilot-test, 주황 띠.
# -*- mode: python ; coding: utf-8 -*-
# macOS build of EQ Autopilot. Build with the Tk 8.6 venv (homebrew python3.9) —
# system Tk 8.5 renders a blank window. Mirrors "EQ Auto-Close.spec" (mac) but for eqgui.py.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = ['autopilot_crypto', 'yaml', 'requests', 'urllib3', 'tzdata', 'tkinter.simpledialog',
                 'keyring', 'keyring.backends.macOS', 'nest_asyncio']
hiddenimports += collect_submodules('eqexec')
hiddenimports += collect_submodules('keyring')
hiddenimports += collect_submodules('cryptography')   # v2 AES-GCM 피드(2026-09-23) - 빠지면 앱이 aead=False를 광고해 v1을 받는다
hiddenimports += collect_submodules('ib_insync')   # IBKR 어댑터(지연 import) — 번들에 포함
hiddenimports += collect_submodules('eventkit')
hiddenimports += ['websocket']   # ProjectX 실시간 허브(websocket-client, 2026-10-16)

a = Analysis(
    ['eqgui.py'],
    pathex=[],
    binaries=[],
    datas=[('flavor_test/eq_flavor.txt', '.'), ('eqlogo.png', '.'), ('eqlogo256.png', '.')] + collect_data_files('tzdata'),
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
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='EQ Autopilot Test',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=['eqicon.icns'],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name='EQ Autopilot Test')
app = BUNDLE(coll, name='EQ Autopilot Test.app', icon='eqicon.icns',
             bundle_identifier='app.edgequant.autopilot.test')
