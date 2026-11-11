"""Build IcePlayer into a stand-alone Windows program.

Usage (from the iceplayer_py folder, with the qtapp environment active):

    python build.py                 # check, test, build, smoke-test
    python build.py --zip           # ...and also make dist/IcePlayer-<date>-<commit>.zip
    python build.py --msi           # ...and also make the installer dist/IcePlayer-<version>.msi
    python build.py --skip-tests    # faster: don't run the test suite first
    python build.py --no-clean      # reuse PyInstaller's cache (faster rebuilds)

Result: ``dist/IcePlayer/IcePlayer.exe``. Copy or zip the whole ``dist/IcePlayer``
folder; the exe needs the ``_internal`` folder next to it. The playlist
(IceList.json) is saved next to the exe.

Steps:
1. Check the environment: PySide6 is importable and not 6.8.x (that Qt can't play
   MP3s with an embedded cover + LAME header), matches requirements.txt, and
   PyInstaller is installed.
2. Run the test suite (``python -m pytest -q``) unless ``--skip-tests``.
3. Run PyInstaller on ``IcePlayer.spec``.
4. Smoke-test the built exe: the skin images are bundled, and the exe starts and
   keeps running for a few seconds (off-screen, with a temporary playlist, so your
   real IceList.json and any running player are not touched).
5. Optionally zip ``dist/IcePlayer`` (``--zip``) and/or wrap it in a Windows
   installer (``--msi``, see msi.py; version from ``iceplayer.__version__``).

Exit code 0 on success, 1 if any step fails.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "IcePlayer.spec"
DIST_DIR = ROOT / "dist" / "IcePlayer"
EXE = DIST_DIR / ("IcePlayer.exe" if os.name == "nt" else "IcePlayer")
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


def build(clean: bool = True):
    """Run PyInstaller on IcePlayer.spec, producing dist/IcePlayer/.

    Args:
        clean: Empty PyInstaller's cache first (slower, but avoids stale files).

    Raises:
        BuildError: if PyInstaller fails or the exe is missing afterwards.
    """
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC.name), "--noconfirm"]
    if clean:
        cmd.append("--clean")
    if run(cmd) != 0:
        raise BuildError("PyInstaller failed (see its output above).")
    if not EXE.is_file():
        raise BuildError(f"PyInstaller finished but {EXE} does not exist.")


def folder_size_mb(folder: Path) -> float:
    """Total size of all files under a folder.

    Args:
        folder: Folder to measure.

    Returns:
        Size in megabytes.
    """
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1024 / 1024


def smoke_test(seconds: float = 4.0):
    """Check the built exe actually works.

    Checks that the skin images were bundled, then starts the exe off-screen with a
    temporary playlist file and a private single-instance name, and checks it is still
    running after ``seconds`` (a missing DLL or import error makes it exit at once).

    Args:
        seconds: How long the exe must stay running.

    Raises:
        BuildError: if images are missing or the exe exits early.
    """
    images = list(DIST_DIR.rglob("iceplayer/resources/background.png"))
    if not images:
        raise BuildError("Skin images are missing from the build (iceplayer/resources).")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ,
               "QT_QPA_PLATFORM": "offscreen",
               "ICEPLAYER_STATE": str(Path(tmp) / "IceList.json"),
               "ICEPLAYER_SERVER": f"iceplayer-build-check-{uuid.uuid4().hex[:8]}"}
        proc = subprocess.Popen([str(EXE)], cwd=DIST_DIR, env=env)
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise BuildError(f"The built exe exited right away (code {proc.returncode}).")
                time.sleep(0.2)
            print(f"IcePlayer.exe started and ran for {seconds:.0f} s: OK")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)


def build_label() -> str:
    """A name for the zip: today's date plus the git commit, e.g. "20261111-dbba0bc".

    Returns:
        "<YYYYMMDD>-<short commit>", with "-dirty" if there are uncommitted changes,
        or just the date when git is not available.
    """
    label = datetime.date.today().strftime("%Y%m%d")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        label += f"-{commit}" + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        pass
    return label


def make_zip() -> Path:
    """Zip dist/IcePlayer for sharing.

    Returns:
        Path of ``dist/IcePlayer-<label>.zip``; unzipping it gives an ``IcePlayer``
        folder containing IcePlayer.exe.
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
    parser.add_argument("--zip", action="store_true", help="also zip dist/IcePlayer")
    parser.add_argument("--msi", action="store_true", help="also make a Windows installer (.msi)")
    args = parser.parse_args(argv)

    started = time.monotonic()
    total = 4 + args.zip + args.msi
    try:
        step(f"1/{total} Checking the environment")
        check_environment()
        step(f"2/{total} Running tests" + (" (skipped)" if args.skip_tests else ""))
        if not args.skip_tests:
            run_tests()
        step(f"3/{total} Building with PyInstaller")
        build(clean=not args.no_clean)
        step(f"4/{total} Smoke-testing the exe")
        smoke_test()
        extras = []
        number = 5
        if args.zip:
            step(f"{number}/{total} Zipping")
            extras.append(make_zip())
            number += 1
        if args.msi:
            step(f"{number}/{total} Making the MSI installer")
            extras.append(make_installer())
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
