"""Tests for the build tools: msi.py (the .msi installer) and helpers in build.py.

What is tested
--------------
1. ``msi.build_msi``  Builds a real .msi from a small fake program folder and reads its
                      tables back: every file is inside (including sub-folders), the
                      product name/version/upgrade code, Start-menu and desktop shortcuts
                      pointing at the exe, the upgrade/downgrade rules, and that the old
                      version is removed before the new one is installed.
2. Bad input          A missing exe or a non-numeric version is refused with a clear error.
3. ``build.py``       Version parsing, the PySide6 pin in requirements.txt, and that the
                      program version is a valid MSI version.

How it is tested
----------------
No PyInstaller run and no installation: the fake folder holds a few small files, so
each MSI builds in well under a second. The MSI-building tests are skipped where
``msilib`` doesn't exist (not Windows, or Python 3.13+). The full build (real exe,
real MSI, unpacked with ``msiexec /a``) is done by ``python build.py --msi``.

How to run
----------
    python -m pytest tests/test_build.py -v
"""
import pytest

from conftest import ROOT

import build

msi = pytest.importorskip("msi", reason="msilib needs Windows and Python <= 3.12")


@pytest.fixture
def fake_dist(tmp_path):
    """A small stand-in for dist/IcePlayer: an exe, a data file and nested folders."""
    d = tmp_path / "dist" / "IcePlayer"
    (d / "_internal" / "iceplayer" / "resources").mkdir(parents=True)
    (d / "IcePlayer.exe").write_bytes(b"MZ fake exe")
    (d / "_internal" / "python312.dll").write_bytes(b"x" * 1000)
    (d / "_internal" / "iceplayer" / "resources" / "background.png").write_bytes(b"png")
    (d / "_internal" / "a very long file name with spaces.txt").write_text("long names work")
    return d


@pytest.fixture
def built(fake_dist, tmp_path):
    """The .msi built from ``fake_dist`` (version 3.4.5)."""
    return msi.build_msi(fake_dist, tmp_path / "out" / "IcePlayer-3.4.5.msi", "3.4.5",
                         icon=ROOT / "iceplayer" / "resources" / "logo.ico")


def props(path):
    """The MSI's Property table as a dict."""
    return dict(msi.read_table(path, "Property", "`Property`, `Value`"))


# =============================================================== build_msi
def test_msi_contains_every_file(built, fake_dist):
    """All 4 files of the folder (including nested sub-folders) are in the File table,
    and one compressed cabinet holds them."""
    names = sorted(r[0].split("|")[-1] for r in msi.read_table(built, "File", "`FileName`"))
    assert names == sorted(p.name for p in fake_dist.rglob("*") if p.is_file())
    assert msi.read_table(built, "Media", "`LastSequence`, `Cabinet`") == [(4, "#distfiles")]


def test_msi_folders_mirror_the_program_folder(built):
    """Sub-folders keep their names: _internal, iceplayer, resources under INSTALLDIR,
    which is "IcePlayer" inside Program Files (64-bit)."""
    dirs = {d: (parent, name) for d, parent, name in
            msi.read_table(built, "Directory", "`Directory`, `Directory_Parent`, `DefaultDir`")}
    assert dirs["INSTALLDIR"][0] == "ProgramFiles64Folder"
    assert dirs["INSTALLDIR"][1].endswith("|IcePlayer")
    long_names = {name.split("|")[-1] for _, name in dirs.values()}
    assert {"_internal", "iceplayer", "resources"} <= long_names


def test_msi_product_properties(built):
    """Name, version, upgrade code, per-machine install and the Settings → Apps icon."""
    p = props(built)
    assert p["ProductName"] == "IcePlayer" and p["ProductVersion"] == "3.4.5"
    assert p["UpgradeCode"] == msi.UPGRADE_CODE
    assert p["ALLUSERS"] == "1" and p["ARPPRODUCTICON"] == "icon.ico"
    assert msi.read_table(built, "Icon", "`Name`") == [("icon.ico",)]


def test_msi_second_build_in_same_process_keeps_standard_folder_ids(fake_dist, tmp_path):
    """Regression: msilib remembers directory ids per process, so a second build used to
    get "TARGETDIR1" / "ProgramFiles64Folder1" and would install to the wrong place."""
    for name in ("first.msi", "second.msi"):
        out = msi.build_msi(fake_dist, tmp_path / name, "1.0.0")
        ids = {r[0] for r in msi.read_table(out, "Directory", "`Directory`")}
        assert {"TARGETDIR", "ProgramFiles64Folder", "INSTALLDIR"} <= ids, f"{name}: {sorted(ids)}"


def test_msi_new_product_code_each_build(fake_dist, tmp_path):
    """Two builds get different ProductCodes (so each is an upgrade) but the same UpgradeCode."""
    a = msi.build_msi(fake_dist, tmp_path / "a.msi", "1.0.0")
    b = msi.build_msi(fake_dist, tmp_path / "b.msi", "1.0.1")
    assert props(a)["ProductCode"] != props(b)["ProductCode"]
    assert props(a)["UpgradeCode"] == props(b)["UpgradeCode"]


def test_msi_shortcuts_start_the_exe(built):
    """Start-menu and desktop shortcuts named "IcePlayer" both start IcePlayer.exe."""
    rows = msi.read_table(built, "Shortcut", "`Shortcut`, `Directory_`, `Name`, `Target`")
    folders = {r[1] for r in rows}
    assert folders == {"ProgramMenuFolder", "DesktopFolder"}
    assert all(r[2].endswith("|IcePlayer") and r[3] == "[#IcePlayer.exe]" for r in rows)


def test_msi_upgrade_rules(built):
    """Older/same versions are removed (OLDPRODUCTFOUND); newer ones only detected
    (NEWERPRODUCTFOUND), which triggers the "newer version installed" error."""
    rows = msi.read_table(built, "Upgrade", "`VersionMin`, `VersionMax`, `Attributes`, `ActionProperty`")
    assert (None, "3.4.5", 513, "OLDPRODUCTFOUND") in rows
    assert ("3.4.5", None, 2, "NEWERPRODUCTFOUND") in rows
    actions = dict(msi.read_table(built, "CustomAction", "`Action`, `Target`"))
    assert "newer version" in actions["PreventDowngrade"]


def test_msi_removes_old_version_first(built):
    """The old version is removed after InstallValidate (1400) and before InstallInitialize
    (1500) — before any new file is copied — and the downgrade check runs right after
    FindRelatedProducts (200)."""
    seq = dict(msi.read_table(built, "InstallExecuteSequence", "`Action`, `Sequence`"))
    assert seq["InstallValidate"] < seq["RemoveExistingProducts"] < seq["InstallInitialize"]
    assert seq["FindRelatedProducts"] < seq["PreventDowngrade"] < seq["InstallValidate"]


def test_msi_replaces_existing_output(fake_dist, tmp_path):
    """Building to an existing .msi path overwrites it instead of failing."""
    out = tmp_path / "x.msi"
    out.write_bytes(b"old junk")
    msi.build_msi(fake_dist, out, "1.0.0")
    assert props(out)["ProductVersion"] == "1.0.0"


# =============================================================== bad input
def test_msi_requires_the_exe(tmp_path):
    """A folder without IcePlayer.exe (exe not built yet) is refused."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="build the exe first"):
        msi.build_msi(tmp_path / "empty", tmp_path / "x.msi", "1.0.0")


@pytest.mark.parametrize("version", ["2", "2.1.0-beta", "v2.1", "1.2.3.4.5", ""])
def test_msi_rejects_invalid_versions(fake_dist, tmp_path, version):
    """MSI versions must be 2-4 plain numbers (e.g. 2.1.0)."""
    with pytest.raises(ValueError, match="numbers"):
        msi.build_msi(fake_dist, tmp_path / "x.msi", version)


# =============================================================== build.py helpers
@pytest.mark.parametrize("text, expected", [("6.10.2", (6, 10, 2)), ("6.8.3", (6, 8, 3)),
                                            ("6.9", (6, 9)), ("6.10.2.140", (6, 10, 2))])
def test_version_tuple(text, expected):
    """Version strings compare as numbers: 6.10 is newer than 6.9 (not as text)."""
    assert build._version_tuple(text) == expected
    assert build._version_tuple("6.10.0") > build._version_tuple("6.9.3")


def test_pinned_pyside_version_matches_requirements():
    """build.py reads the same PySide6 pin that requirements.txt installs, and it meets
    the minimum (6.9+) that build.py demands."""
    pinned = build.pinned_pyside_version()
    assert pinned and pinned in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert build._version_tuple(pinned) >= build.MIN_PYSIDE


def test_app_version_is_a_valid_msi_version():
    """iceplayer.__version__ is plain numbers, so ``build.py --msi`` can use it."""
    version = build.app_version()
    parts = version.split(".")
    assert 2 <= len(parts) <= 4 and all(p.isdigit() for p in parts)
