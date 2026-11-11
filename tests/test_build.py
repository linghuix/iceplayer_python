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
import os
import sys

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


# =============================================================== setup wizard (UI)
# Regression: the first MSI had no dialogs at all, so double-clicking it failed with
# error 2803 / 2726 ("ExitDialog" not found). These tests check that every screen the
# installer can show really exists and every button leads somewhere real.
def _standard_actions():
    """Names of Windows Installer's built-in actions (from msilib's standard sequences)."""
    from msilib import sequence
    names = set()
    for table in ("AdminExecuteSequence", "AdminUISequence", "AdvtExecuteSequence",
                  "InstallExecuteSequence", "InstallUISequence"):
        names |= {row[0] for row in getattr(sequence, table)}
    return names - {"ExitDialog", "FatalError", "UserExit"}   # these must be real dialogs


def _dialogs(path):
    return {r[0] for r in msi.read_table(path, "Dialog", "`Dialog`")}


@pytest.mark.parametrize("table", ["InstallUISequence", "AdminUISequence"])
def test_every_ui_step_exists(built, table):
    """Each step of the UI sequences is a dialog in the MSI, a custom action in the MSI,
    or a built-in Windows Installer action — nothing that would give error 2803/2726."""
    dialogs = _dialogs(built)
    actions = {r[0] for r in msi.read_table(built, "CustomAction", "`Action`")}
    known = dialogs | actions | _standard_actions()
    missing = [r[0] for r in msi.read_table(built, table, "`Action`") if r[0] not in known]
    assert missing == [], f"{table} refers to missing dialogs/actions: {missing}"


def test_required_dialogs_exist(built):
    """The dialogs Windows Installer asks for by name are all there: the wizard pages,
    the end screens, Cancel, the error box and the files-in-use list."""
    assert {"WelcomeDlg", "InstallDirDlg", "ProgressDlg", "ExitDialog", "MaintenanceDlg",
            "UserExit", "FatalError", "CancelDlg", "ErrorDlg", "FilesInUse"} <= _dialogs(built)
    assert props(built)["ErrorDialog"] in _dialogs(built)


def test_wizard_order(built):
    """New install: Welcome first, then Progress; already installed: Repair/Remove first."""
    seq = {a: (c, s) for a, c, s in
           msi.read_table(built, "InstallUISequence", "`Action`, `Condition`, `Sequence`")}
    assert seq["WelcomeDlg"][0] == "NOT Installed"
    assert seq["MaintenanceDlg"][0].startswith("Installed")
    assert seq["CostFinalize"][1] < seq["WelcomeDlg"][1] < seq["ProgressDlg"][1] < seq["ExecuteAction"][1]
    assert [s for a, (c, s) in seq.items() if a == "ExitDialog"] == [-1]


def test_every_button_leads_somewhere_real(built):
    """Buttons that open another screen open a dialog that exists; DoAction runs an
    action that exists (the "Launch IcePlayer" checkbox starts the installed exe)."""
    dialogs = _dialogs(built)
    actions = dict(msi.read_table(built, "CustomAction", "`Action`, `Source`"))
    for dlg, ctl, event, arg in msi.read_table(
            built, "ControlEvent", "`Dialog_`, `Control_`, `Event`, `Argument`"):
        if event in ("NewDialog", "SpawnDialog", "SpawnWaitDialog"):
            assert arg in dialogs, f"{dlg}.{ctl} opens missing dialog {arg}"
        if event == "DoAction":
            assert arg in actions, f"{dlg}.{ctl} runs missing action {arg}"
    assert actions["LaunchApp"] == "IcePlayer.exe"


def test_dialog_focus_default_and_cancel_controls_exist(built):
    """Each dialog's first-focus, Enter and Esc controls, and every Tab-order link,
    point to controls on that dialog (a broken link stops the dialog from opening)."""
    controls = {}
    for dlg, ctl, nxt in msi.read_table(built, "Control", "`Dialog_`, `Control`, `Control_Next`"):
        controls.setdefault(dlg, {})[ctl] = nxt
    for dlg, first, default, cancel in msi.read_table(
            built, "Dialog", "`Dialog`, `Control_First`, `Control_Default`, `Control_Cancel`"):
        for ref in (first, default, cancel):
            assert ref is None or ref in controls[dlg], f"{dlg}: no control {ref}"
        for ctl, nxt in controls[dlg].items():
            assert nxt is None or nxt in controls[dlg], f"{dlg}.{ctl}: Tab goes to missing {nxt}"


def test_tab_order_is_one_loop_per_dialog(built):
    """On every dialog, the controls with a Tab target form ONE closed loop and no two
    controls Tab to the same control.

    Regression: on the install-folder page both the folder box and Cancel Tabbed to
    Back, and Windows Installer refused to open the page (error 2810, setup failed)."""
    tabs = {}
    for dlg, ctl, nxt in msi.read_table(built, "Control", "`Dialog_`, `Control`, `Control_Next`"):
        if nxt is not None:
            tabs.setdefault(dlg, {})[ctl] = nxt
    for dlg, links in tabs.items():
        targets = list(links.values())
        assert len(targets) == len(set(targets)), f"{dlg}: two controls Tab to the same control {links}"
        start = next(iter(links))
        seen, ctl = [], start
        while ctl not in seen:
            seen.append(ctl)
            ctl = links.get(ctl)
            assert ctl is not None, f"{dlg}: Tab chain from {start} ends at a control without a Tab target"
        assert ctl == start and set(seen) == set(links), f"{dlg}: Tab order is not one loop: {links}"


def test_no_literal_ampersand_in_labels(built):
    """Texts drawn without "&" shortcut processing (the NoPrefix attribute) must not
    contain "&", or it shows up literally (the folder label once read "&Folder:")."""
    no_prefix = 0x20000
    bad = [(d, c, t) for d, c, ctype, attr, t in msi.read_table(
        built, "Control", "`Dialog_`, `Control`, `Type`, `Attributes`, `Text`")
        if ctype == "Text" and attr & no_prefix and t and "&" in t]
    assert bad == []


def test_error_box_has_error_icon(built):
    """Windows Installer looks for an "ErrorIcon" control on the error box (log error 2835)."""
    controls = msi.read_table(built, "Control", "`Dialog_`, `Control`, `Type`")
    assert ("ErrorDlg", "ErrorIcon", "Icon") in controls


def test_fonts_used_by_dialogs_exist(built):
    """The default font and the heading font named in dialog texts are defined."""
    styles = {r[0] for r in msi.read_table(built, "TextStyle", "`TextStyle`")}
    assert props(built)["DefaultUIFont"] in styles
    texts = " ".join(r[0] or "" for r in msi.read_table(built, "Control", "`Text`"))
    import re
    assert set(re.findall(r"\{\\(\w+)\}", texts)) <= styles


def test_install_folder_page_edits_installdir(built):
    """The folder box edits INSTALLDIR (through _BrowseProperty), and Install applies it."""
    assert props(built)["_BrowseProperty"] == "INSTALLDIR"
    events = msi.read_table(built, "ControlEvent", "`Dialog_`, `Control_`, `Event`, `Argument`")
    assert ("InstallDirDlg", "Next", "SetTargetPath", "[_BrowseProperty]") in events


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


# =============================================================== portable exe & release
@pytest.mark.parametrize("porcelain, expected", [("", "-portable"), (" M build.py\n", "-portable-dirty")])
def test_build_label_marks_uncommitted_builds(monkeypatch, porcelain, expected):
    """Portable files are named <version>-portable, with -dirty when built from
    uncommitted changes, so a test build is never released by mistake."""
    import subprocess
    from types import SimpleNamespace
    monkeypatch.setattr(build.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=porcelain, returncode=0))
    assert build.build_label() == f"{build.app_version()}{expected}"


def test_write_test_song(tmp_path):
    """The smoke test's song is a valid WAV file of the requested length."""
    import wave
    song = tmp_path / "s.wav"
    build._write_test_song(song, 0.5)
    with wave.open(str(song)) as w:
        assert abs(w.getnframes() / w.getframerate() - 0.5) < 0.01


@pytest.mark.skipif(os.name != "nt", reason="uses taskkill (Windows)")
def test_stop_process_tree_stops_child_processes(tmp_path):
    """Regression: the single-file exe runs as a launcher plus a child process; the
    smoke test used to kill only the launcher and leave IcePlayer running. Here a
    parent Python starts a child Python; stopping the tree must end both."""
    import subprocess
    import time
    pid_file = tmp_path / "child.pid"
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open(r'{pid_file}', 'w').write(str(c.pid)); time.sleep(60)")
    parent = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.monotonic() + 20
    while not (pid_file.is_file() and pid_file.read_text()) and time.monotonic() < deadline:
        time.sleep(0.1)
    child = int(pid_file.read_text())
    build._stop_process_tree(parent)
    time.sleep(0.5)
    alive = subprocess.run(["tasklist", "/FI", f"PID eq {child}", "/NH"],
                           capture_output=True, text=True).stdout
    assert parent.poll() is not None
    assert str(child) not in alive, "child process still running"


def test_release_workflow_uploads_exactly_msi_and_portable_exe():
    """The GitHub workflow builds with --onefile --msi and uploads exactly the MSI and
    the versioned portable exe: not the raw PyInstaller output (IcePlayer-portable.exe)
    and not a -dirty test build."""
    import fnmatch
    import re
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "python build.py --skip-tests --onefile --msi" in text
    line = next(l for l in text.splitlines() if "$files = @(Get-ChildItem" in l)
    patterns = [p.split("\\")[-1] for p in re.findall(r"dist\\([^,\s|]+)", line)]
    raw_output = build.ONEFILE_EXE.name              # what PyInstaller writes before renaming
    names = ["IcePlayer-2.1.3.msi", "IcePlayer-2.1.3-portable.exe", raw_output,
             "IcePlayer-2.1.3-portable-dirty.exe"]
    picked = [n for n in names if any(fnmatch.fnmatch(n, p) for p in patterns)]
    assert picked == ["IcePlayer-2.1.3.msi", "IcePlayer-2.1.3-portable.exe"]


@pytest.mark.skipif(os.name != "nt", reason="PATH cleaning only happens on Windows")
def test_build_env_path_drops_other_pythons_dll_folders(tmp_path, monkeypatch):
    """PyInstaller copies DLLs it finds on PATH. Another Python's DLL folder on PATH
    (e.g. conda base's Library\bin, which Git Bash adds) made it bundle a foreign
    icuuc.dll and the exe failed with "DLL load failed while importing QtCore".
    build_env() must keep only this Python's folders and Windows.

    Input: PATH with a fake foreign Library\bin first. Expected: it is gone; the
    building Python's folder and System32 are kept; nothing else in the env changes.
    """
    foreign = tmp_path / "miniconda3" / "Library" / "bin"
    foreign.mkdir(parents=True)
    monkeypatch.setenv("PATH", os.pathsep.join([str(foreign), os.environ["PATH"]]))
    monkeypatch.setenv("ICEPLAYER_TEST_MARKER", "kept")
    env = build.build_env()
    folders = [p.lower() for p in env["PATH"].split(os.pathsep)]
    assert str(foreign).lower() not in folders
    assert os.path.dirname(sys.executable).lower() in folders
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32").lower()
    assert system32 in folders
    assert env["ICEPLAYER_TEST_MARKER"] == "kept"
    assert os.environ["PATH"].startswith(str(foreign))      # the real env is untouched


def test_check_folder_build_refuses_bundled_icu(tmp_path, monkeypatch):
    """A bundled icuuc.dll stops the exe from starting, so check_folder_build must fail.

    Input: a fake dist folder with the skin image, first without and then with an
    icuuc.dll. Expected: passes without it; BuildError naming the DLL with it.
    """
    dist = tmp_path / "IcePlayer"
    (dist / "_internal" / "iceplayer" / "resources").mkdir(parents=True)
    (dist / "_internal" / "iceplayer" / "resources" / "background.png").write_bytes(b"png")
    monkeypatch.setattr(build, "DIST_DIR", dist)
    build.check_folder_build()                                # fine without ICU
    (dist / "_internal" / "icuuc.dll").write_bytes(b"dll")
    with pytest.raises(build.BuildError, match="icuuc.dll"):
        build.check_folder_build()
