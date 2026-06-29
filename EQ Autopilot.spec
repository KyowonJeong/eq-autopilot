# -*- mode: python ; coding: utf-8 -*-
# macOS build of EQ Autopilot. Build with the Tk 8.6 venv (homebrew python3.9) —
# system Tk 8.5 renders a blank window. Mirrors "EQ Auto-Close.spec" (mac) but for eqgui.py.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['autopilot_crypto', 'yaml', 'requests', 'urllib3', 'tzdata', 'tkinter.simpledialog',
                 'keyring', 'keyring.backends.macOS']
hiddenimports += collect_submodules('eqexec')
hiddenimports += collect_submodules('keyring')

a = Analysis(
    ['eqgui.py'],
    pathex=[],
    binaries=[],
    datas=[('eqlogo.png', '.')],
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
    name='EQ Autopilot',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=['eqicon.icns'],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name='EQ Autopilot')
app = BUNDLE(coll, name='EQ Autopilot.app', icon='eqicon.icns',
             bundle_identifier='app.edgequant.autopilot')
