"""Tests for iceplayer/app.py — starting the app and the "only one player" rule.

What is tested
--------------
1. Single instance   Only one IcePlayer runs at a time. Launching it again (e.g.
                     double-clicking another song) sends the file path to the running
                     player over a local pipe and exits. The running player shows its
                     window and plays that file. With no file, it just comes to the front.
2. Handoff protocol  The sender waits for an "ok" reply, so the message can't be lost;
                     a test checks this takes well under the 3 s timeout.
3. Settings          ``ICEPLAYER_STATE`` / ``ICEPLAYER_SERVER`` environment variables
                     override the playlist file and the pipe name.
4. Real launchers    ``python run.py song`` and ``python -m iceplayer`` start, stay
                     running, save the playlist, and accept a second launch.

How it is tested
----------------
* Each test uses its own random pipe name (``server_name`` fixture), so tests never
  talk to a player you have open, or to each other.
* The "second launch" is a real separate Python process (``subprocess``), because a
  sender inside the same process behaves differently on Windows. Meanwhile the test
  keeps the receiving player's event loop running with ``wait_until``.
* End-to-end tests start ``run.py`` as a real program (off-screen) with a temporary
  playlist file, then check that file to see what the program did. They are slower
  (a few seconds each) because they start real Python processes.

How to run
----------
    python -m pytest tests/test_app.py -v
"""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest

from conftest import ROOT, process, wait_until
from iceplayer import app as app_mod


@pytest.fixture
def server_name(monkeypatch):
    """A unique pipe name for this test, so it never reaches a real running IcePlayer."""
    name = f"iceplayer-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_mod, "SERVER_NAME", name)
    return name


def _send(message, server_name):
    """Act as a second launch: run ``send_to_running_instance(message)`` in a separate
    process while this process keeps serving its event loop.

    Returns (True if the sender found a running player, seconds the sender took).
    """
    code = ("import sys; from iceplayer import app; "
            "print(app.send_to_running_instance(sys.argv[1]))")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "ICEPLAYER_SERVER": server_name}
    start = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-c", code, message], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    assert wait_until(lambda: proc.poll() is not None, 20000), "sender did not finish"
    elapsed = time.monotonic() - start
    process(100)
    return proc.stdout.read().strip() == "True", elapsed


# =============================================================== single instance (in-process receiver)
def test_send_without_running_instance(qapp, server_name):
    """No player running: the sender reports False, so the new launch opens its own window."""
    assert app_mod.send_to_running_instance("x") is False


def test_file_path_with_unicode_and_spaces(player, server_name, music_dir):
    """A path with Chinese characters, spaces and brackets arrives intact."""
    from conftest import make_wav
    song = str(make_wav(music_dir / "我的 歌 (live).wav"))
    app_mod.start_server(player)
    sent, _ = _send(song, server_name)
    assert sent and wait_until(lambda: player.files == [song])


def test_second_instance_hands_over_file(player, server_name, songs):
    """Second launch with a song: the running (hidden) player shows itself, adds and plays it.

    Also checks speed: the sender waits for the "ok" reply, which must arrive promptly
    (well under the 3 s timeout), so a second launch never feels slow.
    """
    server = app_mod.start_server(player)
    assert server.isListening()
    player.hide()
    sent, seconds = _send(songs[1], server_name)
    assert sent
    assert wait_until(lambda: player.current_index == 0 and player.files == [songs[1]])
    assert player.isVisible()
    assert seconds < 2.5, f"handoff took {seconds:.1f}s"


def test_second_instance_without_file_just_raises_window(player, server_name):
    """Second launch with no file (e.g. from the Start menu) leaves mini mode and shows the main window."""
    app_mod.start_server(player)
    player.enter_mini_mode()
    sent, _ = _send("", server_name)
    assert sent
    assert wait_until(player.isVisible)
    assert player.mini_window.isHidden() and player.files == []


def test_start_server_replaces_stale_name(player, server_name):
    """After a crash the old pipe name may linger; starting again must still work."""
    first = app_mod.start_server(player)
    first.close()
    assert app_mod.start_server(player).isListening()


def test_state_file_env_override():
    """ICEPLAYER_STATE and ICEPLAYER_SERVER replace the default playlist file and pipe name.

    How: a fresh Python process imports the app with those variables set and prints the values.
    """
    code = "import iceplayer.app as a; print(a.STATE_FILE); print(a.SERVER_NAME)"
    env = {**os.environ, "ICEPLAYER_STATE": "X:/custom.json", "ICEPLAYER_SERVER": "custom-name"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60).stdout.split()
    assert out[0].replace("\\", "/") == "X:/custom.json" and out[1] == "custom-name"


# =============================================================== end-to-end (real program)
@pytest.fixture
def launch_env(tmp_path):
    """Environment for launching the real app: off-screen, temporary playlist file, private pipe."""
    return {**os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "ICEPLAYER_STATE": str(tmp_path / "IceList.json"),
            "ICEPLAYER_SERVER": f"iceplayer-e2e-{uuid.uuid4().hex[:8]}"}


def test_run_py_end_to_end(launch_env, songs, tmp_path):
    """The whole real-life sequence with two real programs:

    1. ``run.py alpha.wav`` starts, adds the song and saves it to IceList.json.
    2. ``run.py gamma.wav`` (second launch) exits with code 0 after handing over its file.
    3. The first program is still running and now has both songs in IceList.json.
    """
    state = tmp_path / "IceList.json"
    first = subprocess.Popen([sys.executable, "run.py", songs[0]], cwd=ROOT, env=launch_env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while not state.exists() and time.monotonic() < deadline:  # written when the song is added
            assert first.poll() is None, "first instance exited early"
            time.sleep(0.2)
        assert json.loads(state.read_text(encoding="utf-8"))["files"] == [songs[0]]

        second = subprocess.run([sys.executable, "run.py", songs[2]], cwd=ROOT, env=launch_env,
                                capture_output=True, timeout=30)
        assert second.returncode == 0
        assert first.poll() is None, "first instance should keep running"

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if json.loads(state.read_text(encoding="utf-8"))["files"] == [songs[0], songs[2]]:
                break
            time.sleep(0.2)
        assert json.loads(state.read_text(encoding="utf-8"))["files"] == [songs[0], songs[2]]
    finally:
        first.kill()
        first.wait(10)


def test_python_m_iceplayer_starts(launch_env):
    """``python -m iceplayer`` (what VS Code's F5 runs) starts and is still running after 4 s.

    If it crashes (e.g. PySide6 can't load), the failure message shows its error output.
    """
    proc = subprocess.Popen([sys.executable, "-m", "iceplayer"], cwd=ROOT, env=launch_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(4)
        assert proc.poll() is None, proc.stderr.read().decode(errors="replace")
    finally:
        proc.kill()
        proc.wait(10)
