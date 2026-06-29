# -*- mode: python ; coding: utf-8 -*-
# Windows build of EQ Auto-Close (auto-close-only build). Built in CI on windows-latest
# (PyInstaller can't cross-compile from macOS). Output: dist/EQ Auto-Close/.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['yaml', 'requests', 'urllib3', 'tzdata', 'tkinter.simpledialog',
                 'keyring', 'keyring.backends.Windows', 'win32ctypes', 'win32ctypes.pywin32']
hiddenimports += collect_submodules('eqexec')
hiddenimports += collect_submodules('keyring')

a = Analysis(
    ['eqgui_close.py'],
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
    name='EQ Auto-Close',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=['eqicon.ico'],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='EQ Auto-Close')
