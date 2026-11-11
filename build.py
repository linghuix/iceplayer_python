"""Build IcePlayer into a stand-alone Windows program.

Usage (from the iceplayer_py folder, with the qtapp environment active):

    python build.py                 # check, test, build, smoke-test
    python build.py --onefile       # ...and also the single-file dist/IcePlayer-<version>-portable.exe
    python build.py --msi           # ...and also the installer dist/IcePlayer-<version>.msi
    python build.py --zip           # ...and also zip the folder as dist/IcePlayer-<version>-portable.zip
    python build.py --skip-tests    # faster: don't run the test suite first
    python build.py --no-clean      # reuse PyInstaller's cache (faster rebuilds)

Result: ``dist/IcePlayer/IcePlayer.exe`` (folder version: the exe needs the
``_internal`` folder next to it; this is what the MSI installs). With ``--onefile``
also ``dist/IcePlayer-<version>-portable.exe``: ONE file, nothing else needed — it
unpacks itself to a temporary folder at each start. Both save the playlist
(IceList.json) next to the exe.

Steps:
1. Check the environment: PySide6 is importable and not 6.8.x (that Qt can't play
   MP3s with an embedded cover + LAME header), matches requirements.txt, and
   PyInstaller is installed.
2. Run the test suite (``python -m pytest -q``) unless ``--skip-tests``.
3. Run PyInstaller on ``IcePlayer.spec`` (folder version).
4. Smoke-test it: the skin images are bundled, and the exe really starts — it is
   given a short test song and must add it to its playlist file (off-screen, with
   a temporary playlist and a private single-instance name, so your real
   IceList.json and any running player are not touched). Prints the start-up time.
5. ``--onefile``: run PyInstaller on ``IcePlayer-onefile.spec``, smoke-test that exe
   the same way, and copy it to ``dist/IcePlayer-<version>-portable.exe``.
6. Optionally zip the folder (``--zip``) and/or wrap it in a Windows installer
   (``--msi``, see msi.py; version from ``iceplayer.__version__``).

Exit code 0 on success, 1 if any step fails.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "IcePlayer.spec"                      # folder version
DIST_DIR = ROOT / "dist" / "IcePlayer"
EXE = DIST_DIR / ("IcePlayer.exe" if os.name == "nt" else "IcePlayer")
ONEFILE_SPEC = ROOT / "IcePlayer-onefile.spec"      # single-file portable version
ONEFILE_EXE = ROOT / "dist" / ("IcePlayer-portable.exe" if os.name == "nt" else "IcePlayer-portable")
MIN_PYSIDE = (6, 9)   # 6.8.x cannot play MP3s with cover art + LAME header


class BuildError(Exception):
    """A build step failed; the message says what to do about it."""


def step(title: str):
    """Print a step heading.

    Args:
        title: Text to show, e.g. "1/4 Checking the environment".
    """
    print(f"\n=== {title}", flush=True)


def _version_tuple(text: str) -> tuple[int, ...]:
    """Turn "6.10.2" into (6, 10, 2) for comparisons.

    Args:
        text: A dotted version string.

    Returns:
        The numeric parts; non-numeric suffixes are ignored.
    """
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def pinned_pyside_version() -> str | None:
    """Read the PySide6 version pinned in requirements.txt.

    Returns:
        The version after ``PySide6==`` (e.g. "6.10.2"), or None if there is no pin.
    """
    req = ROOT / "requirements.txt"
    if req.is_file():
        m = re.search(r"^PySide6==([\w.]+)", req.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return None


def check_environment():
    """Make sure this Python can build a working IcePlayer.

    Raises:
        BuildError: PySide6 or PyInstaller is missing, or PySide6 is older than 6.9.
    Prints a warning (but continues) if PySide6 differs from requirements.txt.
    """
    print(f"Python      {sys.version.split()[0]}  ({sys.executable})")
    try:
        import PySide6
        from PySide6 import QtCore, QtMultimedia  # noqa: F401  (must load, not just exist)
    except ImportError as e:
        raise BuildError(f"PySide6 can't be imported ({e}).\n"
                         "  Activate the qtapp env, or: pip install -r requirements-dev.txt")
    version = PySide6.__version__
    print(f"PySide6     {version}  (Qt {QtCore.qVersion()})")
    if _version_tuple(version) < MIN_PYSIDE:
        raise BuildError(f"PySide6 {version} is too old: Qt 6.8 can't play MP3s that have an "
                         "embedded cover and a LAME header.\n"
                         "  Install the pinned version: pip install -r requirements.txt")
    pinned = pinned_pyside_version()
    if pinned and pinned != version:
        print(f"WARNING: requirements.txt pins PySide6=={pinned}, but {version} is installed.")
    try:
        import PyInstaller
    except ImportError:
        raise BuildError("PyInstaller is not installed.\n"
                         "  pip install -r requirements-dev.txt")
    print(f"PyInstaller {PyInstaller.__version__}")


def run(cmd: list[str], **kwargs) -> int:
    """Run a command in the project folder, showing its output live.

    Args:
        cmd:    Program and arguments, e.g. ``[sys.executable, "-m", "pytest"]``.
        kwargs: Extra ``subprocess.run`` options (e.g. ``env``).

    Returns:
        The command's exit code.
    """
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, **kwargs).returncode


def run_tests():
    """Run the whole test suite with the current Python.

    Raises:
        BuildError: if any test fails (so a broken player is never packaged).
    """
    if run([sys.executable, "-m", "pytest", "-q"]) != 0:
        raise BuildError("Tests failed; not building. Fix them, or use --skip-tests.")


def build_env() -> dict[str, str]:
    """Environment for PyInstaller with a clean, predictable PATH.

    PyInstaller looks for the DLLs a program needs along PATH and copies what it
    finds into the build. If PATH contains another Python's DLL folder (for example
    the conda base env's ``Library\\bin``, which Git Bash puts on PATH), it copies
    that env's ``icuuc.dll`` (ICU 73). At run time Qt then loads it instead of
    Windows' own ICU and stops with "DLL load failed while importing QtCore". So on
    Windows PATH is reduced to the building Python's own folders plus Windows.

    Returns:
        A copy of ``os.environ`` with PATH replaced (unchanged on other systems).
    """
    env = dict(os.environ)
    if os.name != "nt":
        return env
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    folders = [Path(sys.executable).parent]
    for prefix in (Path(sys.prefix), Path(sys.base_prefix)):     # differ in a venv
        folders += [prefix, prefix / "Library" / "bin", prefix / "Scripts"]
    folders += [windows / "System32", windows, windows / "System32" / "Wbem"]
    seen, path = set(), []
    for f in folders:
        if f.is_dir() and str(f).lower() not in seen:
            seen.add(str(f).lower())
            path.append(str(f))
    env["PATH"] = os.pathsep.join(path)
    return env


def build(clean: bool = True, spec: Path = SPEC, expected: Path = EXE):
    """Run PyInstaller on a spec file.

    Args:
        clean:    Empty PyInstaller's cache first (slower, but avoids stale files).
        spec:     ``IcePlayer.spec`` (folder version, default) or
                  ``IcePlayer-onefile.spec`` (single-file version).
        expected: The exe that spec must produce.

    Raises:
        BuildError: if PyInstaller fails or the exe is missing afterwards.
    """
    cmd = [sys.executable, "-m", "PyInstaller", str(spec.name), "--noconfirm"]
    if clean:
        cmd.append("--clean")
    if run(cmd, env=build_env()) != 0:      # clean PATH: see build_env
        raise BuildError("PyInstaller failed (see its output above).")
    if not expected.is_file():
        raise BuildError(f"PyInstaller finished but {expected} does not exist.")


def folder_size_mb(folder: Path) -> float:
    """Total size of all files under a folder.

    Args:
        folder: Folder to measure.

    Returns:
        Size in megabytes.
    """
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1024 / 1024


def _write_test_song(path: Path, seconds: float = 0.5):
    """Write a short silent WAV file for the smoke test.

    Args:
        path:    File to create.
        seconds: Length of the silence.
    """
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(bytes(2 * int(22050 * seconds)))


def smoke_test(exe: Path = EXE, timeout: float = 60.0) -> float:
    """Check that a built exe really starts and works.

    Starts ``exe`` off-screen with a short test song as its argument (like
    double-clicking a song), a temporary playlist file and a private single-instance
    name. The exe must add the song to the playlist file — which only happens after
    Qt, the window and the player have all started — and keep running.

    Args:
        exe:     The exe to test (folder version or single-file version).
        timeout: Seconds to wait for the playlist file before giving up.

    The single-file exe runs as two processes (a launcher that unpacks everything to
    a ``_MEI...`` temp folder, and the real program), so the whole process tree is
    stopped afterwards, and TEMP points into this test's own folder so the unpacked
    files are deleted with it.

    Returns:
        Seconds from launch until the song was in the playlist (the start-up time).

    Raises:
        BuildError: if the exe exits, or doesn't add the song within ``timeout``.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        song = Path(tmp) / "smoke test song.wav"
        _write_test_song(song)
        state = Path(tmp) / "IceList.json"
        unpack = Path(tmp) / "temp"
        unpack.mkdir()
        env = {**os.environ,
               "QT_QPA_PLATFORM": "offscreen",
               "TEMP": str(unpack), "TMP": str(unpack),     # single-file exe unpacks here
               "ICEPLAYER_STATE": str(state),
               "ICEPLAYER_SERVER": f"iceplayer-build-check-{uuid.uuid4().hex[:8]}"}
        started = time.monotonic()
        proc = subprocess.Popen([str(exe), str(song)], cwd=exe.parent, env=env)
        try:
            while not (state.is_file() and song.name in state.read_text(encoding="utf-8", errors="replace")):
                if proc.poll() is not None:
                    raise BuildError(f"{exe.name} exited during start-up (code {proc.returncode}).")
                if time.monotonic() - started > timeout:
                    raise BuildError(f"{exe.name} did not finish starting within {timeout:.0f} s.")
                time.sleep(0.1)
            seconds = time.monotonic() - started
            time.sleep(1.0)
            if proc.poll() is not None:
                raise BuildError(f"{exe.name} exited right after starting (code {proc.returncode}).")
            print(f"{exe.name} started and added the test song in {seconds:.1f} s: OK")
            return seconds
        finally:
            _stop_process_tree(proc)


def _stop_process_tree(proc: subprocess.Popen):
    """Stop a process and every process it started (the single-file exe's launcher
    starts the real program as a child; killing only the launcher would leave the
    program running).

    Args:
        proc: The process started by ``subprocess.Popen``.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)       # fails harmlessly if already gone
    elif proc.poll() is None:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def check_folder_build():
    """Check the folder version: skin images bundled, no foreign ICU DLL.

    Raises:
        BuildError: if ``iceplayer/resources/background.png`` is missing in dist/IcePlayer,
            or an ``icuuc.dll`` was bundled (Qt must use Windows' own ICU; a bundled one
            comes from another Python on PATH and stops the exe from starting).
    """
    if not list(DIST_DIR.rglob("iceplayer/resources/background.png")):
        raise BuildError("Skin images are missing from the build (iceplayer/resources).")
    icu = [p.name for p in DIST_DIR.rglob("icu*.dll")]
    if icu:
        raise BuildError(f"Foreign ICU DLLs were bundled ({', '.join(icu)}); they come from "
                         "another Python on PATH and stop Qt from loading.")


def make_portable_exe() -> Path:
    """Copy the single-file exe to its release name.

    Returns:
        Path of ``dist/IcePlayer-<version>-portable.exe`` (``-dirty`` added when built
        from uncommitted changes, see ``build_label``).
    """
    target = ROOT / "dist" / f"IcePlayer-{build_label()}.exe"
    shutil.copy2(ONEFILE_EXE, target)
    return target


def build_label() -> str:
    """A name for the portable exe / zip: the program version, e.g. "2.1.2-portable".

    Returns:
        "<version>-portable", plus "-dirty" when built from uncommitted changes (so a
        test build is never mistaken for a release).
    """
    label = f"{app_version()}-portable"
    try:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        if dirty:
            label += "-dirty"
    except (OSError, subprocess.CalledProcessError):
        pass   # not a git checkout: just the version
    return label


def make_zip() -> Path:
    """Zip dist/IcePlayer as the portable ("green") version: unzip anywhere and run
    IcePlayer.exe, no installation; the playlist is saved next to the exe.

    Returns:
        Path of ``dist/IcePlayer-<version>-portable.zip``; unzipping it gives an
        ``IcePlayer`` folder containing IcePlayer.exe.
    """
    base = ROOT / "dist" / f"IcePlayer-{build_label()}"
    return Path(shutil.make_archive(str(base), "zip", root_dir=DIST_DIR.parent, base_dir="IcePlayer"))


def app_version() -> str:
    """Returns: the program version from ``iceplayer/__init__.py``, e.g. "2.1.0"."""
    sys.path.insert(0, str(ROOT))
    from iceplayer import __version__
    return __version__


def make_installer() -> Path:
    """Wrap dist/IcePlayer in a Windows installer.

    Returns:
        Path of ``dist/IcePlayer-<version>.msi``.

    Raises:
        BuildError: msilib is unavailable (not Windows, or Python 3.13+) or the
        version number is not valid for an MSI.
    """
    try:
        import msi
    except ImportError as e:
        raise BuildError(f"Can't make an MSI with this Python ({e}). msilib needs Windows and "
                         "Python 3.12 or older; use the qtapp environment.")
    version = app_version()
    output = ROOT / "dist" / f"IcePlayer-{version}.msi"
    try:
        msi.build_msi(DIST_DIR, output, version, icon=ROOT / "iceplayer" / "resources" / "logo.ico")
    except (ValueError, FileNotFoundError) as e:
        raise BuildError(str(e))
    files = len(msi.read_table(output, "File", "`File`"))
    print(f"MSI version {version} with {files} files: {output.name}")
    return output


def main(argv=None) -> int:
    """Run the build steps.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``); see the module docstring.

    Returns:
        0 on success, 1 if a step failed.
    """
    parser = argparse.ArgumentParser(description="Build IcePlayer.exe with PyInstaller.")
    parser.add_argument("--skip-tests", action="store_true", help="don't run the test suite first")
    parser.add_argument("--no-clean", action="store_true", help="keep PyInstaller's cache")
    parser.add_argument("--onefile", action="store_true",
                        help="also make the single-file portable exe")
    parser.add_argument("--zip", action="store_true", help="also zip dist/IcePlayer")
    parser.add_argument("--msi", action="store_true", help="also make a Windows installer (.msi)")
    args = parser.parse_args(argv)

    steps = [("Checking the environment", check_environment),
             ("Running tests" + (" (skipped)" if args.skip_tests else ""),
              None if args.skip_tests else run_tests),
             ("Building the folder version with PyInstaller", lambda: build(clean=not args.no_clean)),
             ("Smoke-testing the folder version", lambda: (check_folder_build(), smoke_test(EXE)))]
    extras = []
    if args.onefile:
        steps += [("Building the single-file portable exe",
                   lambda: build(not args.no_clean, ONEFILE_SPEC, ONEFILE_EXE)),
                  ("Smoke-testing the portable exe", lambda: smoke_test(ONEFILE_EXE)),
                  ("Naming the portable exe", lambda: extras.append(make_portable_exe()))]
    if args.zip:
        steps.append(("Zipping the folder version", lambda: extras.append(make_zip())))
    if args.msi:
        steps.append(("Making the MSI installer", lambda: extras.append(make_installer())))

    started = time.monotonic()
    try:
        for number, (title, action) in enumerate(steps, start=1):
            step(f"{number}/{len(steps)} {title}")
            if action is not None:
                action()
    except BuildError as e:
        print(f"\nBUILD FAILED: {e}", file=sys.stderr)
        return 1

    print(f"\nBUILD OK in {time.monotonic() - started:.0f} s")
    print(f"  {EXE}  ({folder_size_mb(DIST_DIR):.0f} MB folder)")
    for path in extras:
        print(f"  {path}  ({path.stat().st_size / 1024 / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
