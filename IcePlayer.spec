# PyInstaller build recipe:  pyinstaller IcePlayer.spec
# Produces dist/IcePlayer/IcePlayer.exe (a folder you can zip and copy anywhere).
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
    [],
    exclude_binaries=True,
    name="IcePlayer",
    icon="iceplayer/resources/logo.ico",
    console=False,          # GUI app: no black console window
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="IcePlayer", upx=False)
