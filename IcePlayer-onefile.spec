# PyInstaller recipe for the single-file portable exe:  python build.py --onefile
# Produces dist/IcePlayer-portable.exe (build.py then copies it to
# dist/IcePlayer-<version>-portable.exe). Everything — Python, Qt, the skin images —
# is packed inside the exe; at each start it unpacks itself to a temporary folder
# and deletes it again when IcePlayer closes. The playlist (IceList.json) is saved
# next to the exe. IcePlayer.spec makes the normal folder version (used by the MSI).
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["run.py"],
    pathex=[],
    datas=[("iceplayer/resources", "iceplayer/resources")],  # skin images
    hiddenimports=[],
    excludes=["PyQt5", "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,             # onefile: the DLLs and data go INTO the exe ...
    a.datas,
    [],
    name="IcePlayer-portable",
    icon="iceplayer/resources/logo.ico",
    console=False,          # GUI app: no black console window
    upx=False,
    runtime_tmpdir=None,    # unpack into the user's normal temp folder
)
# ... and there is no COLLECT step, so no dist/<name>/ folder is made.
