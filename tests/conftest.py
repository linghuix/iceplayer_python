"""Shared test setup, loaded automatically by pytest before any test file.

What lives here
---------------
* Environment setup: Qt is switched to the "offscreen" platform so tests never open
  real windows on your screen, and noisy Qt debug logging is turned off.
* Fixtures (pytest's reusable test ingredients, requested by naming them as test
  function arguments):
    - ``qapp``      one QApplication shared by the whole run (Qt needs exactly one).
    - ``music_dir`` a temporary folder with three generated songs, one .lrc lyric file
                    and one non-audio file.
    - ``songs``     the three song paths from ``music_dir``, in a fixed order.
    - ``player``    a fresh main window (IcePlayer) using a temporary playlist file,
                    cleaned up after each test.
* Helpers imported by the test files:
    - ``process(ms)``          let Qt handle events for ``ms`` milliseconds.
    - ``wait_until(cond, ms)`` keep handling events until ``cond()`` is true or time runs out.
    - ``make_wav(path, secs)`` write a short, quiet sine-tone WAV file.

Why the helpers exist
---------------------
Qt does its work (signals, timers, audio playback) inside an event loop. A test that
starts playback and immediately checks the result would see nothing yet, so tests call
``wait_until(...)`` to pump the event loop until the expected state appears.

Nothing here touches your real playlist (IceList.json): every player gets its own
file inside pytest's ``tmp_path`` folder, which is deleted afterwards.
"""
import math
import os
import struct
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

# Must be set before PySide6 is imported: draw windows in memory, not on screen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.multimedia.*=false")

# Make "import iceplayer" work no matter which folder pytest was started from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """The single QApplication for the whole test run.

    Also installs a safety net: every 300 ms it closes any popup menu or file dialog
    that a test opened by mistake. Without this, a real ``QMenu.exec()`` would wait
    forever for a mouse click that never comes and the whole run would hang.
    """
    app = QApplication.instance() or QApplication([])
    guard = QTimer()
    guard.timeout.connect(_close_blocking_windows)
    guard.start(300)
    yield app
    guard.stop()


def _close_blocking_windows():
    """Close a stray popup menu or file dialog (see ``qapp``)."""
    popup = QApplication.activePopupWidget()
    if popup:
        popup.close()
    modal = QApplication.activeModalWidget()
    if modal is not None and type(modal).__name__ == "QFileDialog":
        modal.reject()


def process(ms: int = 0):
    """Run the Qt event loop for ``ms`` milliseconds (0 = handle what is queued now)."""
    end = time.monotonic() + ms / 1000
    QApplication.processEvents()
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout_ms: int = 5000) -> bool:
    """Pump the event loop until ``predicate()`` is true; return False on timeout.

    Used as ``assert wait_until(lambda: player.is_playing())`` so a test waits for
    asynchronous things (audio starting, a song ending) without fixed sleeps.
    """
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_wav(path: Path, seconds: float = 1.0, rate: int = 22050):
    """Write a quiet stereo 440 Hz sine tone, so tests have real audio to play."""
    frames = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            s = int(1000 * math.sin(2 * math.pi * 440 * i / rate))
            data += struct.pack("<hh", s, s)
        w.writeframes(bytes(data))
    return path


@pytest.fixture
def music_dir(tmp_path):
    """A temporary music folder:

    ============  ========  ===========================================
    alpha.wav     1.0 s     plain song, no lyrics
    测试歌曲.wav   1.5 s     Chinese file name, has 测试歌曲.lrc (GBK)
    gamma.wav     1.0 s     plain song
    notes.txt               not audio, must be ignored by folder/drop adds
    ============  ========  ===========================================

    The lyric file shows "第一行" from 0.00 s and "第二行" from 0.80 s.
    """
    d = tmp_path / "music"
    d.mkdir()
    make_wav(d / "alpha.wav", 1.0)
    make_wav(d / "测试歌曲.wav", 1.5)
    make_wav(d / "gamma.wav", 1.0)
    (d / "测试歌曲.lrc").write_text("[00:00.00]第一行\n[00:00.80]第二行\n", encoding="gbk")
    (d / "notes.txt").write_text("not music")
    return d


@pytest.fixture
def songs(music_dir):
    """The three song paths as strings: [alpha, 测试歌曲, gamma]."""
    return [str(music_dir / n) for n in ("alpha.wav", "测试歌曲.wav", "gamma.wav")]


@pytest.fixture
def player(qapp, tmp_path, monkeypatch):
    """A new IcePlayer main window for one test, saving to ``tmp_path/state.json``.

    ``closeEvent`` normally calls ``QApplication.quit()``; that is replaced with a
    no-op so closing a player inside a test doesn't end the shared QApplication.
    After the test, the song is unloaded and all windows are closed and deleted.
    """
    from iceplayer import player as player_mod
    monkeypatch.setattr(player_mod, "QApplication", SimpleNamespace(quit=lambda: None))
    p = player_mod.IcePlayer(tmp_path / "state.json")
    yield p
    p.unload()
    p.close()
    p.lyric_window.close()
    p.mini_window.close()
    p.deleteLater()
    process(20)
