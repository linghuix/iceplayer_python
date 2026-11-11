"""Tests for iceplayer/playlist.py — play modes and the saved playlist file.

What is tested
--------------
1. ``is_audio``        Which files count as music (by extension, case-insensitive).
2. ``next_index``      Which song plays next, for every play mode:
                         单曲播放 SINGLE       – stop when the song ends
                         列表循环 LIST_LOOP    – go through the list, wrap to the start
                         单曲循环 SINGLE_LOOP  – repeat the same song
                         随机播放 RANDOM       – any other song
                       ``auto=True`` means the song ended by itself; ``auto=False``
                       means the user pressed Next (which always moves on).
3. ``previous_index``  The Previous button, including wrap-around.
4. ``load_state`` / ``save_state``
                       Reading/writing IceList.json ({"files", "volume", "mode"}),
                       including broken, missing or hand-edited files.
5. Double-click to play
                       Double-clicking a row in the main window's playlist plays that
                       song; a deleted song shows 失效; empty space does nothing.

How it is tested
----------------
Sections 1-4 are pure functions (no windows, no audio), so each test calls the
function with example inputs and checks the return value. Random mode is tested by
calling it many times and checking the set of results (it must never pick the
current song). File tests use pytest's ``tmp_path``: a fresh empty folder per test.

Section 5 uses a real off-screen player (``player`` fixture) with generated songs,
sends the same mouse events a real double-click produces, and waits for the audio to
start (see conftest.py).

How to run
----------
    python -m pytest tests/test_playlist.py -v
"""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from conftest import process, wait_until
from iceplayer.player import INVALID
from iceplayer.playlist import (AUDIO_EXTENSIONS, MODE_NAMES, PlayMode, is_audio, load_state,
                                next_index, previous_index, save_state)


# =============================================================== is_audio
@pytest.mark.parametrize("name", ["a.mp3", "B.MP3", "c.flac", "d.wav", "e.ogg", "f.m4a",
                                  "g.aac", "h.wma", "i.opus", "C:/dir.with.dots/song.Mp3"])
def test_is_audio_true(name):
    """Every supported extension is accepted, in any letter case, even with dots in folders."""
    assert is_audio(name)


@pytest.mark.parametrize("name", ["a.txt", "a.lrc", "a.jpg", "mp3", "a.mp3.bak", ""])
def test_is_audio_false(name):
    """Lyrics, covers, text, backups, a bare "mp3" and an empty name are not music."""
    assert not is_audio(name)


def test_mode_names_match_modes():
    """The four Chinese menu labels line up with the four PlayMode values (0..3)."""
    assert len(MODE_NAMES) == len(PlayMode)
    assert MODE_NAMES == ["单曲播放", "列表循环", "单曲循环", "随机播放"]
    assert ".mp3" in AUDIO_EXTENSIONS


# =============================================================== next_index
@pytest.mark.parametrize("mode", list(PlayMode))
@pytest.mark.parametrize("auto", [True, False])
def test_next_index_empty_list(mode, auto):
    """Empty playlist: nothing to play in any mode (8 combinations)."""
    assert next_index(0, 0, mode, auto) is None


def test_next_single_auto_stops():
    """单曲播放: when the song ends by itself, playback stops (None)."""
    assert next_index(1, 3, PlayMode.SINGLE, auto=True) is None


def test_next_single_loop_auto_repeats():
    """单曲循环: when the song ends, the same index plays again."""
    assert next_index(1, 3, PlayMode.SINGLE_LOOP, auto=True) == 1


@pytest.mark.parametrize("mode", [PlayMode.SINGLE, PlayMode.SINGLE_LOOP, PlayMode.LIST_LOOP])
def test_next_button_is_sequential_and_wraps(mode):
    """Pressing Next always goes to the following song, wrapping last -> first."""
    assert next_index(0, 3, mode, auto=False) == 1
    assert next_index(2, 3, mode, auto=False) == 0


def test_next_list_loop_auto_wraps():
    """列表循环: after the last song ends, the first one starts."""
    assert next_index(2, 3, PlayMode.LIST_LOOP, auto=True) == 0


def test_next_from_nothing_selected_starts_at_first():
    """Index -1 means "nothing playing yet": Next starts with song 0."""
    assert next_index(-1, 3, PlayMode.LIST_LOOP, auto=False) == 0


@pytest.mark.parametrize("auto", [True, False])
def test_next_random_never_repeats_current(auto):
    """随机播放: 200 picks from song 1 of 4 only ever give 0, 2 or 3, and all of them appear."""
    picks = {next_index(1, 4, PlayMode.RANDOM, auto) for _ in range(200)}
    assert picks == {0, 2, 3}


def test_next_random_single_song():
    """随机播放 with one song: it can only repeat itself (no endless search)."""
    assert next_index(0, 1, PlayMode.RANDOM, auto=True) == 0


# =============================================================== previous_index
def test_previous_wraps():
    """Previous from the first song goes to the last; otherwise one step back."""
    assert previous_index(0, 3, PlayMode.LIST_LOOP) == 2
    assert previous_index(2, 3, PlayMode.SINGLE) == 1


def test_previous_empty():
    """Previous on an empty playlist does nothing."""
    assert previous_index(0, 0, PlayMode.LIST_LOOP) is None


def test_previous_random_never_repeats_current():
    """In random mode Previous also picks a different song."""
    assert {previous_index(2, 3, PlayMode.RANDOM) for _ in range(100)} == {0, 1}


# =============================================================== state file (IceList.json)
DEFAULT = {"files": [], "volume": 100, "mode": 0}


def test_load_state_missing_file(tmp_path):
    """First start (no file yet): empty playlist, full volume, 单曲播放."""
    assert load_state(tmp_path / "none.json") == DEFAULT


@pytest.mark.parametrize("content", ["not json", "", "[1, 2]", '"str"', "null"])
def test_load_state_bad_content_gives_defaults(tmp_path, content):
    """A corrupt or wrongly shaped file falls back to defaults instead of crashing."""
    f = tmp_path / "s.json"
    f.write_text(content, encoding="utf-8")
    assert load_state(f) == DEFAULT


def test_load_state_filters_and_clamps(tmp_path):
    """Non-string entries are dropped from the list and volume 250 becomes 100."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"files": ["a.mp3", 3, None, "b.mp3"], "volume": 250, "mode": 2}))
    assert load_state(f) == {"files": ["a.mp3", "b.mp3"], "volume": 100, "mode": 2}


@pytest.mark.parametrize("volume, expected", [(-5, 0), (55, 55), ("loud", 100)])
def test_load_state_volume(tmp_path, volume, expected):
    """Volume is clamped to 0..100; a non-number falls back to 100."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"volume": volume}))
    assert load_state(f)["volume"] == expected


@pytest.mark.parametrize("mode", [9, -1, "1", None])
def test_load_state_invalid_mode_defaults(tmp_path, mode):
    """An unknown play mode (out of range, text, null) falls back to 单曲播放 (0)."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mode": mode}))
    assert load_state(f)["mode"] == 0


def test_save_then_load_roundtrip_with_unicode(tmp_path):
    """What is saved loads back identically; Chinese paths are stored readable, not \\u-escaped."""
    f = tmp_path / "s.json"
    save_state(f, ["D:/音乐/歌.mp3"], 42, PlayMode.RANDOM)
    assert load_state(f) == {"files": ["D:/音乐/歌.mp3"], "volume": 42, "mode": 3}
    assert "音乐" in f.read_text(encoding="utf-8")


def test_save_state_unwritable_path_does_not_raise(tmp_path):
    """If the file can't be written (folder missing, read-only drive) the player keeps running."""
    save_state(tmp_path / "missing_dir" / "s.json", [], 50, 0)


# =============================================================== double-click to play
def _status(player, row):
    """The playlist's second column for a row: "" normally, "失效" when the file is unusable."""
    return player.table.item(row, 1).text()


def _wait_playing(player, row):
    """Wait until audio is really playing (position moving) and check it is ``row``."""
    assert wait_until(lambda: player.is_playing() and player.media.position() > 0), \
        "playback did not start"
    assert player.current_index == row


def _real_double_click(widget, pos):
    """Simulate a real mouse double-click at ``pos`` on ``widget``.

    Sends the same event sequence a real mouse produces: press, release,
    double-click, release. ``QTest.mouseDClick`` alone is not enough in Qt 6: it
    sends only the double-click event, and Qt's table ignores a double-click that
    wasn't preceded by a press.
    """
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, pos)     # press + release
    QTest.mouseDClick(widget, Qt.LeftButton, Qt.NoModifier, pos)    # double-click
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, pos)   # release


def _double_click_row(player, row, col=0):
    """Double-click the centre of a playlist cell, as a user would.

    Args:
        player: The main window (it is shown so the table has real positions).
        row:    Playlist row.
        col:    0 = song name column, 1 = narrow status column.
    """
    player.show()
    pos = player.table.visualItemRect(player.table.item(row, col)).center()
    _real_double_click(player.table.viewport(), pos)


def test_double_click_row_plays_it(player, songs):
    """Double-clicking the 3rd song in the playlist plays it (row turns bold, Pause shows).

    This checks the connection table.cellDoubleClicked -> play_index, which tests
    calling play_index directly don't cover.
    """
    player.add_files(songs)
    _double_click_row(player, 2)
    _wait_playing(player, 2)
    assert player.table.item(2, 0).font().bold()
    assert player.pause_button.isVisibleTo(player)


def test_double_click_other_row_switches_song(player, songs):
    """While one song plays, double-clicking another row switches to it."""
    player.add_files(songs)
    _double_click_row(player, 0)
    _wait_playing(player, 0)
    _double_click_row(player, 1)
    _wait_playing(player, 1)
    assert not player.table.item(0, 0).font().bold()


def test_double_click_status_column_also_plays(player, songs):
    """Double-clicking the narrow status column (2nd column) of a row plays it too."""
    player.add_files(songs)
    _double_click_row(player, 1, col=1)
    _wait_playing(player, 1)


def test_double_click_missing_file_marks_invalid(player, songs):
    """Double-clicking a song whose file was deleted shows 失效 and plays nothing."""
    player.add_files(songs)
    Path(songs[0]).unlink()            # delete the file after it was added
    _double_click_row(player, 0)
    process(200)
    assert _status(player, 0) == INVALID
    assert not player.is_playing() and player.current_index == -1


def test_double_click_empty_area_does_nothing(player, songs):
    """Double-clicking empty space below the songs plays nothing."""
    player.add_files(songs)
    player.show()
    _real_double_click(player.table.viewport(), QPoint(50, 300))
    process(200)
    assert not player.is_playing() and player.current_index == -1
