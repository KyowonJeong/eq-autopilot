# -*- mode: python ; coding: utf-8 -*-
# macOS build of EQ Auto-Close (auto-close-only build). Build with the Tk 8.6 venv
# (homebrew python3.9) — system Tk 8.5 renders a blank window.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = ['autopilot_crypto', 'yaml', 'requests', 'urllib3', 'tzdata', 'tkinter.simpledialog']
hiddenimports += collect_submodules('eqexec')

a = Analysis(
    ['eqgui_close.py'],
    pathex=[],
    binaries=[],
    datas=[('eqlogo.png', '.')] + collect_data_files('tzdata'),
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
    name='EQ Auto-Close',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=['eqicon.icns'],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name='EQ Auto-Close')
app = BUNDLE(coll, name='EQ Auto-Close.app', icon='eqicon.icns',
             bundle_identifier='app.edgequant.autoclose')
