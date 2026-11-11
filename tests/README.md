# IcePlayer tests

350 automated tests that check every part of the player: lyric files, play modes,
the saved playlist, every button and menu, real audio playback (WAV and MP3), the
mini window, and the real `run.py` launcher. A full run takes about 30 seconds.

## How to run

All commands are run from the `iceplayer_py` folder, using the `qtapp` conda
environment (it has PySide6 6.10.2 and pytest installed):

```bash
conda activate qtapp
```

```bash
python -m pytest
```

You should see a row of dots and finally `350 passed`. Each dot is one passed test;
`F` is a failure and `E` is an error in the test setup.

### In VS Code

* **Test Explorer**: click the beaker icon in the left sidebar. Click ▶ next to a
  file, a test, or the whole suite. Failed tests turn red; click one to see why.
* **Ctrl+Shift+P → "Tasks: Run Test Task"** (or **Terminal → Run Task… → Test: all**)
  runs everything in the terminal.
* **Debug a test**: open a test file, put a breakpoint (click left of a line number),
  then **Run and Debug → "Current test file" → F5**. Execution stops at your breakpoint.
  You can also right-click a test in Test Explorer → **Debug Test**.

### Useful command-line options

| Command | What it does |
|---|---|
| `python -m pytest -v` | One line per test with its full name (instead of dots) |
| `python -m pytest tests/test_lyrics.py` | Only one file |
| `python -m pytest tests/test_player.py::test_next_and_previous` | Only one test |
| `python -m pytest -k "seek or pause"` | Only tests whose name contains "seek" or "pause" |
| `python -m pytest -x` | Stop at the first failure |
| `python -m pytest --durations=10` | Also list the 10 slowest tests |
| `python -m pytest -s` | Show `print()` output from tests |

## What each file tests

| File | Tests | What it covers |
|---|---|---|
| `conftest.py` | (none) | Shared setup: off-screen Qt, temporary songs/playlist, helper functions |
| `test_lyrics.py` | 45 | Lyric file encodings (UTF-8/UTF-16/GBK), `[mm:ss.xx]` time tags, which line shows when, the desktop lyric bar |
| `test_playlist.py` | 59 | Which files are music, next/previous in all 4 play modes, saving and loading `IceList.json`, double-clicking a playlist row to play it |
| `test_widgets.py` | 61 | Every skin image loads, hover buttons, scrolling title, click-to-seek slider, volume popup, dragging the window |
| `test_player.py` | 122 | The main window: add/remove/clear, playlist colours and exact background opacity (also under a dark system theme), dialogs, right-click menu, play/pause/fade, next/prev, end-of-song per mode, progress slider (follows playback, drag to seek, click jumps to the mouse), real-world MP3s (tags, embedded cover, LAME header), lyrics sync, covers, drag & drop, closing |
| `test_mini.py` | 16 | About box, mini window buttons and menu, keeping it in sync with the main window |
| `test_app.py` | 15 | Only one player at a time (a second launch hands its file over), where the playlist is saved (source, portable exe, installed exe), the real `run.py` / `python -m iceplayer` |
| `test_build.py` | 32 | The MSI installer builder: every file and folder inside, product name/version, Start-menu and desktop shortcuts, upgrade/downgrade rules, the setup wizard (every dialog, button target and Tab loop exists); `build.py` version checks |

(Counts include every example of a parametrized test. Every file starts with a
docstring explaining what it tests and how, and every test has a docstring saying
what it checks.)

## How the tests work

* **No windows on screen.** `conftest.py` sets `QT_QPA_PLATFORM=offscreen`, so Qt
  draws windows in memory. Tests can still click buttons and take "screenshots"
  with `grab()`.
* **Real audio.** Tests generate short WAV files (1 to 1.5 s) and really play
  them, so a sound device must be present (any normal PC has one).
* **Waiting instead of sleeping.** Playback happens in the background, so tests use
  `wait_until(lambda: condition)`, which keeps Qt running until the condition is
  true (or fails after a few seconds).
* **Dialogs and menus are faked.** A real file dialog or popup menu would wait for a
  person. Tests replace them with `unittest.mock.patch`, which returns a chosen
  answer or records that the menu was opened. As a safety net, `conftest.py` also
  closes any real popup that appears by mistake.
* **Your data is safe.** Every test uses a fresh temporary folder (pytest's
  `tmp_path`) for songs and the playlist file, and its own private pipe name. The
  tests never read or change your real `IceList.json` and never contact a player
  you have open.
* **Hangs can't block you.** `pytest.ini` stops any test that runs longer than 60 s and
  prints where it was stuck.

## When a test fails

pytest prints the failing test's name, the line of the failing `assert`, and the
values involved, for example:

```
tests/test_player.py:303: in test_next_and_previous
    assert player.current_index == 1
E   assert 2 == 1
```

That means: after pressing Next once, the player was on song 2 but the test expected
song 1. Read the test's docstring for what behaviour it expects, then look at the
matching code in `iceplayer/`.

If the run stops with `Timeout (0:01:00)!`, a test hung. The traceback below it
shows the line where it was stuck.

## Adding a test

Add a function whose name starts with `test_` to the matching file. Ask for
fixtures by naming them as arguments:

```python
def test_volume_zero_is_silent(player):
    """Volume 0 on the button gives 0.0 on the real audio output."""
    player.set_volume(0)
    assert player.audio.volume() == 0
```

Run it on its own with `python -m pytest -k volume_zero -v`.
