"""Tests for iceplayer/player.py — the 400x600 main window (IcePlayer).

What is tested (sections below follow this order)
-------------------------------------------------
1.  ``fmt_ms``             Time label format (mm:ss).
2.  Start-up               Empty window looks right; saved playlist/volume/mode come back.
3.  Playlist editing       Add (no duplicates, missing files marked 失效), remove, clear,
                           striped row colours, playlist saved after each change.
4.  Dialogs                "添加歌曲" (files) and "添加目录" (folder), including Cancel.
5.  Right-click menu       Items shown on empty space vs. on a song; "移除本曲".
6.  Playback               Play a row, missing/broken files, play/pause with volume fade,
                           Space key, next/previous, skipping missing files.
7.  Play modes             End-of-song behaviour for 单曲播放 / 列表循环 / 单曲循环 / 随机播放,
                           both simulated and with real short songs.
8.  Volume, seek, progress Slider range, time label, seeking on release / groove click.
    + Freeze regression    Pause → seek → clear/remove/close used to hang Qt 6.8 forever.
    + Real slider use      Slider follows real playback to the end; dragging the knob with
                           the mouse seeks only on release; clicking the bar jumps forward;
                           switching songs resets the range.
9.  Song info              Title/artist/album fallbacks, long-title scrolling, lyrics
                           following playback, cover art from song.jpg or default.
10. Other windows          Lyric bar toggle, mini mode, About, minimise, add button.
11. Open & drag-and-drop   "Open with" a file; dropping files and folders on the window.
12. Closing                Close saves playlist, volume and mode, and closes side windows.

How it is tested
----------------
* Every test gets a fresh, off-screen player from the ``player`` fixture and three
  short generated songs from ``songs`` (see conftest.py).
* Real audio really plays (1–1.5 s WAV files). Because playback is asynchronous,
  tests wait with ``wait_until(...)`` / ``wait_playing(...)`` instead of sleeping.
* Some tests call internal handlers directly (e.g. ``_on_media_status(EndOfMedia)``
  to simulate "the song just ended") so the behaviour can be checked instantly and
  reliably; the ``test_real_song_end_*`` tests confirm the same with real songs.
* File dialogs and popup menus would wait for a person, so they are replaced with
  ``unittest.mock.patch`` that returns a chosen answer or records the call.

How to run
----------
    python -m pytest tests/test_player.py -v
    python -m pytest tests/test_player.py -k "seek or pause"     # a subset by name
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent, QPixmap, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QSlider, QStyle, QStyleOptionSlider

from conftest import make_jpeg, make_mp3, make_wav, process, wait_until
from iceplayer import player as player_mod
from iceplayer.player import INVALID, ROW_EVEN, ROW_ODD, IcePlayer, fmt_ms
from iceplayer.playlist import PlayMode


# --------------------------------------------------------------- small helpers
def names(p):
    """Song names as shown in the playlist's first column, top to bottom."""
    return [p.table.item(r, 0).text() for r in range(p.table.rowCount())]


def status(p, row):
    """The playlist's second column for a row: "" normally, "失效" when the file is unusable."""
    return p.table.item(row, 1).text()


def wait_playing(p, row=None):
    """Wait until audio is really playing (position moving); optionally check which row."""
    assert wait_until(lambda: p.is_playing() and p.media.position() > 0), "playback did not start"
    if row is not None:
        assert p.current_index == row


# =============================================================== 1. fmt_ms
@pytest.mark.parametrize("ms, text", [(0, "00:00"), (999, "00:00"), (1000, "00:01"),
                                      (61000, "01:01"), (3599000, "59:59"), (-50, "00:00")])
def test_fmt_ms(ms, text):
    """Milliseconds -> "mm:ss"; partial seconds round down, negatives show 00:00."""
    assert fmt_ms(ms) == text


# =============================================================== 2. start-up
def test_initial_window(player):
    """A new player: 400x600 frameless window accepting drops, empty list, 单曲播放,
    volume 100, placeholder labels, Play (not Pause) button, progress bar disabled."""
    assert player.size().toTuple() == (400, 600)
    assert player.windowFlags() & Qt.FramelessWindowHint
    assert player.acceptDrops()
    assert player.files == [] and player.current_index == -1
    assert player.play_mode == PlayMode.SINGLE
    assert player.volume_button.volume() == 100
    assert (player.name_label.text(), player.artist_label.text(),
            player.album_label.text(), player.time_label.text()) == ("Name", "Musician", "Album", "00:00")
    assert player.play_button.isVisibleTo(player) and not player.pause_button.isVisibleTo(player)
    assert not player.progress.isEnabled()


def test_init_does_not_wipe_saved_state(qapp, tmp_path, songs):
    """Regression: starting the player must not overwrite the saved playlist with an
    empty one before it has been loaded."""
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"files": songs, "volume": 30, "mode": 1}), encoding="utf-8")
    p = IcePlayer(state)
    assert json.loads(state.read_text(encoding="utf-8"))["files"] == songs
    p.deleteLater()


def test_restores_saved_state(qapp, tmp_path, songs):
    """Playlist, volume (30 -> audio 0.30) and play mode (随机播放) are restored from the file."""
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"files": songs, "volume": 30, "mode": 3}), encoding="utf-8")
    p = IcePlayer(state)
    assert p.files == [str(Path(s)) for s in songs]
    assert p.volume_button.volume() == 30
    assert abs(p.audio.volume() - 0.30) < 0.01
    assert p.play_mode == PlayMode.RANDOM
    assert p.mode_group.checkedAction().text() == "随机播放"
    p.deleteLater()


# =============================================================== 3. playlist editing
def test_add_files(player, songs):
    """Added songs appear by file name (no extension), none marked 失效."""
    player.add_files(songs)
    assert names(player) == ["alpha", "测试歌曲", "gamma"]
    assert all(status(player, r) == "" for r in range(3))


def test_add_files_skips_duplicates(player, songs):
    """Adding songs already in the list doesn't add them twice."""
    player.add_files(songs)
    player.add_files(songs[:2])
    assert player.table.rowCount() == 3 and len(player.files) == 3


def test_add_missing_file_marked_invalid(player, tmp_path):
    """A path that doesn't exist is still listed, but shows 失效."""
    player.add_files([str(tmp_path / "gone.mp3")])
    assert status(player, 0) == INVALID


def test_add_files_saves_state(player, songs):
    """Adding songs immediately writes them to the playlist file."""
    player.add_files(songs)
    saved = json.loads(player.state_file.read_text(encoding="utf-8"))
    assert saved["files"] == player.files


def test_add_files_no_save(player, songs):
    """``save=False`` (used while loading at start-up) doesn't write the file."""
    player.add_files(songs, save=False)
    assert not player.state_file.exists()


def row_pixel(p, row):
    """The colour actually drawn in the middle of a playlist row (right of the text),
    as (r, g, b, alpha)."""
    rect = p.table.visualItemRect(p.table.item(row, 0))
    c = p.table.grab().toImage().pixelColor(rect.x() + 280, rect.center().y())
    return c.red(), c.green(), c.blue(), c.alpha()


def empty_pixel(p):
    """The colour drawn in the empty area below the last row, as (r, g, b, alpha)."""
    c = p.table.grab().toImage().pixelColor(100, p.table.viewport().height() - 10)
    return c.red(), c.green(), c.blue(), c.alpha()


def rgba(color):
    return color.red(), color.green(), color.blue(), color.alpha()


def test_rows_alternate_colours(player, songs):
    """Rows are striped as drawn on screen: light blue, white, light blue (like the
    original), and the empty area below is white — all fully opaque by default."""
    player.add_files(songs)
    assert [row_pixel(player, r) for r in range(3)] == [rgba(ROW_EVEN), rgba(ROW_ODD), rgba(ROW_EVEN)]
    assert empty_pixel(player) == rgba(ROW_ODD)
    assert ROW_EVEN.alpha() == ROW_ODD.alpha() == player_mod.PLAYLIST_ALPHA == 255


def test_remove_row_before_current(player, songs):
    """Removing a song above the playing one shifts the playing index up by one."""
    player.add_files(songs)
    player.current_index = 2
    player.remove_row(0)
    assert player.current_index == 1 and names(player) == ["测试歌曲", "gamma"]


def test_remove_row_after_current(player, songs):
    """Removing a song below the playing one leaves the playing index alone."""
    player.add_files(songs)
    player.current_index = 0
    player.remove_row(2)
    assert player.current_index == 0 and len(player.files) == 2


def test_remove_playing_row_stops(player, songs):
    """Removing the song that is playing stops playback and resets the song info."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.remove_row(1)
    assert player.current_index == -1
    assert wait_until(lambda: not player.is_playing())
    assert player.name_label.text() == "Name"


@pytest.mark.parametrize("row", [-1, 3, 99])
def test_remove_row_out_of_range_is_ignored(player, songs, row):
    """Invalid row numbers are ignored instead of crashing."""
    player.add_files(songs)
    player.remove_row(row)
    assert len(player.files) == 3


def test_remove_row_updates_colours_and_state(player, songs):
    """After removing, stripes are recomputed and the file is saved with 2 songs."""
    player.add_files(songs)
    player.remove_row(0)
    assert row_pixel(player, 0) == rgba(ROW_EVEN)     # the new first row is blue again
    assert row_pixel(player, 1) == rgba(ROW_ODD)
    assert len(json.loads(player.state_file.read_text(encoding="utf-8"))["files"]) == 2


# --- Explicit playlist colours / opacity ---------------------------------------
# On another computer the playlist looked see-through with grey text (Windows dark
# mode / different style). Every colour and the background opacity are now fixed in
# code: PlaylistTable paints the background once with an exact alpha.
@pytest.mark.parametrize("alpha", [0, 64, 128, 180, 255])
def test_playlist_opacity_is_exact(player, songs, alpha):
    """``set_playlist_alpha(a)`` makes stripes, white rows and the empty area exactly
    alpha ``a`` on screen — layers don't stack up into a more opaque result.

    The colour channels may differ by a couple of units at low alpha: Qt stores
    translucent pixels premultiplied (colour × alpha), so reading them back rounds."""
    player.add_files(songs)
    player.set_playlist_alpha(alpha)

    def close(actual, expected_rgb):
        return actual[3] == alpha and (alpha == 0 or all(
            abs(a - e) <= 3 for a, e in zip(actual[:3], expected_rgb)))

    assert close(row_pixel(player, 0), rgba(ROW_EVEN)[:3]), row_pixel(player, 0)
    assert close(row_pixel(player, 1), rgba(ROW_ODD)[:3]), row_pixel(player, 1)
    assert close(empty_pixel(player), rgba(ROW_ODD)[:3]), empty_pixel(player)
    assert player.playlist_alpha == alpha


@pytest.mark.parametrize("value, expected", [(-20, 0), (300, 255)])
def test_playlist_opacity_is_clamped(player, value, expected):
    """Out-of-range opacities are limited to 0..255."""
    player.set_playlist_alpha(value)
    assert player.playlist_alpha == expected


def test_playlist_ignores_dark_system_palette(qapp, tmp_path, songs, monkeypatch):
    """With a dark-mode application palette (dark backgrounds, white text — what
    Windows dark mode gives a Qt app), the playlist still draws blue/white opaque rows
    and black text."""
    from types import SimpleNamespace
    from PySide6.QtGui import QPalette
    monkeypatch.setattr(player_mod, "QApplication", SimpleNamespace(quit=lambda: None))
    saved = qapp.palette()
    dark = QPalette()
    for role, name in ((QPalette.Window, "#202020"), (QPalette.Base, "#1e1e1e"),
                       (QPalette.AlternateBase, "#2d2d2d"), (QPalette.Text, "#ffffff"),
                       (QPalette.WindowText, "#ffffff"), (QPalette.Highlight, "#0078d4"),
                       (QPalette.HighlightedText, "#ffffff")):
        dark.setColor(role, QColor(name))
    qapp.setPalette(dark)
    try:
        p = IcePlayer(tmp_path / "s.json")
        p.add_files(songs)
        p.show()
        process(50)
        assert row_pixel(p, 0) == rgba(ROW_EVEN) and row_pixel(p, 1) == rgba(ROW_ODD)
        assert empty_pixel(p) == rgba(ROW_ODD)
        assert p.table.palette().color(QPalette.Text) == player_mod.PLAYLIST_TEXT
        assert p.table.item(0, 0).foreground().color() == player_mod.PLAYLIST_TEXT
        p.close()
        p.deleteLater()
    finally:
        qapp.setPalette(saved)


def test_playlist_text_colours_are_explicit(player, songs, tmp_path):
    """Song names are black and the 失效 marker red, set on every row (not taken from
    the system palette)."""
    player.add_files(songs + [str(tmp_path / "gone.mp3")])
    for row in range(4):
        assert player.table.item(row, 0).foreground().color() == player_mod.PLAYLIST_TEXT
        assert player.table.item(row, 1).foreground().color() == player_mod.INVALID_TEXT


def test_selected_row_stays_opaque(player, songs):
    """Even with a see-through playlist, the selected row is drawn in solid light blue."""
    player.add_files(songs)
    player.set_playlist_alpha(100)
    player.show()
    player.table.selectRow(1)
    process(50)
    assert row_pixel(player, 1) == rgba(player_mod.SELECTION_BG)


def test_app_forces_light_colours(qapp):
    """``use_light_colors`` switches the app to the light scheme (Qt 6.8+), so Windows
    dark mode can't restyle menus, dialogs or the playlist."""
    from iceplayer.app import use_light_colors
    assert use_light_colors(qapp) is True


def test_clear_list(player, songs):
    """移除所有: stops playback, empties the list and saves the empty list."""
    player.add_files(songs)
    player.play_index(0)
    player.clear_list()
    assert player.files == [] and player.table.rowCount() == 0 and player.current_index == -1
    assert wait_until(lambda: not player.is_playing())
    assert json.loads(player.state_file.read_text(encoding="utf-8"))["files"] == []


# =============================================================== 4. dialogs
# The real file dialogs would wait for a person, so ``patch.object`` swaps them for a
# stand-in that immediately returns the answer the test wants.
def test_open_files_dialog(player, songs):
    """添加歌曲: the files chosen in the dialog are added."""
    with patch.object(player_mod.QFileDialog, "getOpenFileNames", return_value=(songs[:2], "")):
        player.open_files()
    assert len(player.files) == 2


def test_open_files_dialog_cancelled(player):
    """添加歌曲 → Cancel: nothing is added."""
    with patch.object(player_mod.QFileDialog, "getOpenFileNames", return_value=([], "")):
        player.open_files()
    assert player.files == []


def test_open_dir_adds_only_audio_sorted(player, music_dir):
    """添加目录: adds only the audio files of the folder (not .txt/.lrc), sorted by name."""
    with patch.object(player_mod.QFileDialog, "getExistingDirectory", return_value=str(music_dir)):
        player.open_dir()
    assert sorted(names(player)) == names(player)
    assert set(names(player)) == {"alpha", "测试歌曲", "gamma"}


def test_open_dir_cancelled(player):
    """添加目录 → Cancel: nothing is added."""
    with patch.object(player_mod.QFileDialog, "getExistingDirectory", return_value=""):
        player.open_dir()
    assert player.files == []


# =============================================================== 5. right-click menu
def _menu_texts(player, pos):
    """Right-click at ``pos`` in the playlist and return the menu item labels.

    The real menu would wait for a click, so ``exec`` is replaced by a function that
    just reads the items.
    """
    seen = []
    with patch.object(player.table_menu, "exec",
                      lambda *a: seen.extend(x.text() for x in player.table_menu.actions() if x.text())):
        player._table_menu(pos)
    return seen


def test_table_menu_on_empty_area(player):
    """Right-click on empty space: add songs, add folder, remove all (no "remove this")."""
    assert _menu_texts(player, QPoint(10, 300)) == ["添加歌曲", "添加目录", "移除所有"]


def test_table_menu_on_song(player, songs):
    """Right-click on a song: the extra "移除本曲" (remove this song) item appears."""
    player.add_files(songs)
    pos = player.table.visualItemRect(player.table.item(1, 0)).center()
    assert _menu_texts(player, pos) == ["添加歌曲", "添加目录", "移除本曲", "移除所有"]


def test_table_menu_remove_this_song(player, songs):
    """Choosing 移除本曲 on the middle song removes exactly that song."""
    player.add_files(songs)
    pos = player.table.visualItemRect(player.table.item(1, 0)).center()

    def trigger_remove(*_):
        [a for a in player.table_menu.actions() if a.text() == "移除本曲"][0].trigger()

    with patch.object(player.table_menu, "exec", trigger_remove):
        player._table_menu(pos)
    assert names(player) == ["alpha", "gamma"]


# =============================================================== 6. playback
def test_play_index_plays(player, songs):
    """Playing row 2: audio starts, Pause button replaces Play, that row turns bold."""
    player.add_files(songs)
    assert player.play_index(2)
    wait_playing(player, 2)
    assert player.pause_button.isVisibleTo(player) and not player.play_button.isVisibleTo(player)
    assert player.table.item(2, 0).font().bold()
    assert not player.table.item(0, 0).font().bold()


def test_play_index_missing_file(player, songs, tmp_path):
    """Trying to play a deleted file fails, marks it 失效 and plays nothing."""
    player.add_files(songs + [str(tmp_path / "gone.mp3")])
    assert not player.play_index(3)
    assert status(player, 3) == INVALID and player.current_index == -1


@pytest.mark.parametrize("row", [-1, 5])
def test_play_index_out_of_range(player, songs, row):
    """Invalid row numbers return False instead of crashing."""
    player.add_files(songs)
    assert not player.play_index(row)


def test_invalid_mark_cleared_when_file_returns(player, music_dir):
    """A 失效 song that reappears on disk (e.g. USB drive plugged back in) plays and loses the mark."""
    path = music_dir / "later.wav"
    player.add_files([str(path)])
    assert status(player, 0) == INVALID
    make_wav(path)
    assert player.play_index(0)
    assert status(player, 0) == ""


def test_play_clicked_from_stopped_starts_first_song(player, songs):
    """Play with nothing playing yet starts the first song."""
    player.add_files(songs)
    player.play_clicked()
    wait_playing(player, 0)


def test_play_clicked_with_empty_list_does_nothing(player):
    """Play with an empty playlist does nothing (no error)."""
    player.play_clicked()
    process(100)
    assert not player.is_playing()


def test_pause_fades_then_pauses_and_restores_volume(player, songs):
    """Pause fades the sound out (~0.3 s) before pausing, then puts the volume back to 80
    so the next Play isn't silent."""
    player.add_files(songs)
    player.set_volume(80)
    player.play_index(1)
    wait_playing(player)
    player.pause_clicked()
    assert player._fade_timer.isActive()
    assert wait_until(lambda: player.media.playbackState() == QMediaPlayer.PausedState, 3000)
    assert abs(player.audio.volume() - 0.8) < 0.01
    assert player.play_button.isVisibleTo(player)


def test_resume_after_pause(player, songs):
    """Play after Pause resumes playback."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.pause_clicked()
    assert wait_until(lambda: player.media.playbackState() == QMediaPlayer.PausedState, 3000)
    player.play_clicked()
    assert wait_until(player.is_playing)


def test_play_during_fade_cancels_fade(player, songs):
    """Stopping a fade halfway restores full volume (no stuck half-volume)."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.pause_clicked()
    player.play_clicked()   # still playing, fade in progress
    player._stop_fade()
    assert not player._fade_timer.isActive()
    assert abs(player.audio.volume() - 1.0) < 0.01


def test_toggle_play_and_space_shortcut(player, songs):
    """The Space key starts playback; pressing it again (toggle) pauses."""
    player.add_files(songs)
    shortcut = player.findChildren(QShortcut)[0]
    assert shortcut.key().toString() == "Space"
    shortcut.activated.emit()
    wait_playing(player)
    player.toggle_play()
    assert wait_until(lambda: player.media.playbackState() == QMediaPlayer.PausedState, 3000)


def test_next_and_previous(player, songs):
    """Next: 0 -> 1 -> 2 -> 0 (wraps). Previous from 0 wraps to 2."""
    player.add_files(songs)
    player.set_play_mode(PlayMode.LIST_LOOP)
    player.play_index(0)
    player.next_clicked()
    assert player.current_index == 1
    player.next_clicked(); player.next_clicked()
    assert player.current_index == 0
    player.previous_clicked()
    assert player.current_index == 2


def test_previous_with_nothing_playing_goes_to_last(player, songs):
    """Previous before anything played starts the last song."""
    player.add_files(songs)
    player.previous_clicked()
    assert player.current_index == 2


def test_next_skips_missing_files(player, songs, tmp_path):
    """Next jumps over two deleted files to the next real song, marking both 失效."""
    player.add_files([songs[0], str(tmp_path / "gone1.mp3"), str(tmp_path / "gone2.mp3"), songs[2]])
    player.play_index(0)
    player.next_clicked()
    assert player.current_index == 3
    assert status(player, 1) == INVALID and status(player, 2) == INVALID


def test_next_when_all_missing_does_not_loop_forever(player, tmp_path):
    """If every file is missing, Next gives up after one round instead of hanging."""
    player.add_files([str(tmp_path / f"gone{i}.mp3") for i in range(3)])
    player.next_clicked()
    assert player.current_index == -1


def test_next_on_empty_list(player):
    """Next/Previous on an empty playlist do nothing."""
    player.next_clicked()
    player.previous_clicked()
    assert player.current_index == -1


# =============================================================== 7. play modes / end of song
def test_set_play_mode(player):
    """Setting a mode updates the player, emits ``mode_changed``, ticks the menu item, and saves."""
    got = []
    player.mode_changed.connect(got.append)
    player.set_play_mode(PlayMode.SINGLE_LOOP)
    assert player.play_mode == PlayMode.SINGLE_LOOP and got == [2]
    assert player.mode_group.checkedAction().text() == "单曲循环"
    assert json.loads(player.state_file.read_text(encoding="utf-8"))["mode"] == 2


def test_mode_menu_action_sets_mode(player):
    """Choosing 列表循环 in the play-mode menu switches the mode."""
    [a for a in player.mode_group.actions() if a.text() == "列表循环"][0].trigger()
    assert player.play_mode == PlayMode.LIST_LOOP


def test_mode_button_opens_menu(player):
    """The play-mode button opens the mode menu (mocked so it doesn't wait for a click)."""
    with patch.object(player.mode_menu, "exec") as exec_:
        player.mode_button.click()
    exec_.assert_called_once()


# The next four simulate "the song just ended" by calling the handler directly.
def test_end_of_song_single_mode_stops(player, songs):
    """单曲播放: at the end of a song nothing new starts."""
    player.add_files(songs)
    player.play_index(0)
    player._on_media_status(QMediaPlayer.EndOfMedia)
    assert player.current_index == 0


def test_end_of_song_list_loop_advances(player, songs):
    """列表循环: after the last song, the first song starts."""
    player.add_files(songs)
    player.set_play_mode(PlayMode.LIST_LOOP)
    player.play_index(2)
    player._on_media_status(QMediaPlayer.EndOfMedia)
    assert player.current_index == 0


def test_end_of_song_single_loop_restarts(player, songs):
    """单曲循环: the same song jumps back to 0:00 (checked by spying on setPosition)."""
    player.add_files(songs)
    player.set_play_mode(PlayMode.SINGLE_LOOP)
    player.play_index(1)
    wait_playing(player)
    with patch.object(player.media, "setPosition") as set_pos:
        player._on_media_status(QMediaPlayer.EndOfMedia)
    set_pos.assert_called_once_with(0)
    assert player.current_index == 1


def test_end_of_song_random_picks_other(player, songs):
    """随机播放: a different song starts."""
    player.add_files(songs)
    player.set_play_mode(PlayMode.RANDOM)
    player.play_index(1)
    player._on_media_status(QMediaPlayer.EndOfMedia)
    assert player.current_index in (0, 2)


# These two let a real 1-second song play to its end.
def test_real_song_end_advances_in_list_loop(player, songs):
    """列表循环 with real audio: when alpha (1 s) finishes, song 2 starts by itself."""
    player.add_files(songs)
    player.set_play_mode(PlayMode.LIST_LOOP)
    player.play_index(0)
    assert wait_until(lambda: player.current_index == 1, 6000)


def test_real_song_end_stops_in_single_mode(player, songs):
    """单曲播放 with real audio: the song ends, playback stops, Play button returns."""
    player.add_files(songs)
    player.play_index(0)
    assert wait_until(lambda: player.media.mediaStatus() == QMediaPlayer.EndOfMedia, 6000)
    assert player.current_index == 0 and not player.is_playing()
    assert player.play_button.isVisibleTo(player)


def test_invalid_media_marks_row(player, music_dir):
    """A file that exists but isn't audio (renamed text) is marked 失效 when played."""
    bad = music_dir / "broken.mp3"
    bad.write_bytes(b"this is not audio" * 100)
    player.add_files([str(bad)])
    player.play_index(0)
    assert wait_until(lambda: status(player, 0) == INVALID, 5000)


# =============================================================== 8. volume / seek / progress
def test_set_volume(player):
    """Volume 25 on the button = 0.25 on the real audio output."""
    player.set_volume(25)
    assert player.volume_button.volume() == 25
    assert abs(player.audio.volume() - 0.25) < 0.01


def test_duration_sets_slider(player):
    """Song length sets the progress bar range; clicking the groove jumps 1/10 of the song.
    A zero-length song disables the bar."""
    player._on_duration(90000)
    assert player.progress.maximum() == 90000 and player.progress.isEnabled()
    assert player.progress.pageStep() == 9000
    player._on_duration(0)
    assert not player.progress.isEnabled()


def test_position_updates_slider_and_time(player):
    """While playing, the bar and the time label follow the position (65 s -> "01:05")."""
    player._on_duration(100000)
    player._on_position(65000)
    assert player.progress.value() == 65000 and player.time_label.text() == "01:05"


def test_position_does_not_move_slider_while_dragging(player):
    """While the user holds the slider, playback updates don't yank it away from the mouse."""
    player._on_duration(100000)
    player.progress.setValue(10)
    player.progress.setSliderDown(True)
    player._on_position(50000)
    assert player.progress.value() == 10
    player.progress.setSliderDown(False)


def test_seek_on_slider_release(player, songs):
    """Releasing the slider jumps playback to the slider position (spy on setPosition)."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.progress.setValue(1000)
    with patch.object(player.media, "setPosition") as set_pos:
        player._seek_to_slider()
    set_pos.assert_called_once_with(1000)


def test_seek_while_paused_resumes(player, songs):
    """Dragging the slider while paused jumps there and resumes playing (as the original did)."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.media.pause()
    assert wait_until(lambda: not player.is_playing())
    player.progress.setValue(200)
    player._seek_to_slider()
    assert wait_until(player.is_playing)


def _pause_and_seek(player, songs):
    """Get into the state that used to freeze Qt 6.8: playing -> paused -> position changed."""
    player.add_files(songs)
    player.play_index(1)
    wait_playing(player)
    player.media.pause()
    assert wait_until(lambda: player.media.playbackState() == QMediaPlayer.PausedState)
    player.media.setPosition(300)        # what clicking the progress bar while paused does
    process(50)


# Regression tests: with Qt 6.8, QMediaPlayer.stop() after seeking while paused blocked
# forever, freezing the app. The player now unloads the song before stopping. If the
# bug came back, these tests would hang and pytest's faulthandler_timeout would stop
# the run with a traceback pointing here.
def test_clear_list_after_paused_seek_does_not_freeze(player, songs):
    """Pause, click the progress bar, then 移除所有: completes and stops playback."""
    _pause_and_seek(player, songs)
    player.clear_list()
    assert player.media.playbackState() == QMediaPlayer.StoppedState


def test_remove_playing_row_after_paused_seek_does_not_freeze(player, songs):
    """Pause, seek, then remove the current song: completes."""
    _pause_and_seek(player, songs)
    player.remove_row(1)
    assert player.current_index == -1


def test_close_after_paused_seek_does_not_freeze(player, songs):
    """Pause, seek, then close the window: completes and saves the playlist."""
    _pause_and_seek(player, songs)
    player.show()
    player.close()
    assert player.state_file.exists()


def test_switch_song_after_paused_seek(player, songs):
    """Pause, seek, then double-click another song: it plays."""
    _pause_and_seek(player, songs)
    player.play_index(2)
    wait_playing(player, 2)


def test_click_on_slider_groove_seeks(player):
    """A page step (Page Up / Page Down keys) seeks at once by 1/10 of the song.

    Regression: page steps used to do nothing because the slider action arrives as a
    plain int. (Mouse clicks now jump straight to the mouse instead; see the
    ``test_click_on_bar_*`` tests below.)"""
    player._on_duration(10000)
    with patch.object(player.media, "setPosition") as set_pos:
        player.progress.triggerAction(QSlider.SliderPageStepAdd)
        process(30)
    set_pos.assert_called_once_with(1000)


def test_slider_drag_action_does_not_seek_immediately(player):
    """Dragging the handle doesn't seek on every pixel; seeking waits for the release."""
    player._on_duration(10000)
    with patch.object(player.media, "setPosition") as set_pos:
        player._on_slider_click(QSlider.SliderMove)
        process(30)
    set_pos.assert_not_called()


# --- The progress slider with real playback and real mouse input -----------------
# The tests above feed the player made-up positions or call its handlers directly.
# These play real audio and use the mouse like a user, so they also cover the Qt
# connections (positionChanged -> slider, mouse release -> seek, click -> jump).
def _slider_rects(slider):
    """Where the slider's groove (the bar) and handle (the knob) are drawn, in pixels.

    Returns:
        (groove QRect, handle QRect), asked from the slider's own style, so the
        positions are right whatever Qt style is active.
    """
    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    style = slider.style()
    groove = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, slider)
    handle = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, slider)
    return groove, handle


def _x_for_value(slider, value):
    """The x pixel where the handle's centre sits when the slider shows ``value``."""
    groove, handle = _slider_rects(slider)
    span = groove.width() - handle.width()
    offset = QStyle.sliderPositionFromValue(slider.minimum(), slider.maximum(), value, span)
    return groove.x() + offset + handle.width() // 2


def _mouse(slider, kind, pos, buttons):
    """Send one mouse event (press/move/release) at ``pos`` to the slider, with the
    left button held according to ``buttons`` — the events a real mouse produces."""
    ev = QMouseEvent(kind, QPointF(pos), QPointF(slider.mapToGlobal(pos)),
                     Qt.LeftButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(slider, ev)
    process(10)


def _pause_at_start(player, path):
    """Play ``path``, then pause just after it starts, so positions only change by seeking."""
    player.add_files([str(path)])
    player.play_index(0)
    wait_playing(player)
    player.media.pause()
    assert wait_until(lambda: player.media.playbackState() == QMediaPlayer.PausedState)
    assert wait_until(lambda: player.progress.maximum() > 0)
    player.show()


def test_slider_follows_real_playback(player, music_dir):
    """While a real 4 s song plays, the slider's range is the song length, its value keeps
    rising with the playback position, and the time label counts up to 00:01."""
    song = make_wav(music_dir / "four_seconds.wav", 4.0)
    player.add_files([str(song)])
    player.play_index(0)
    assert wait_until(lambda: player.progress.maximum() >= 3900), "slider range not set to song length"
    assert player.progress.isEnabled()
    assert wait_until(lambda: player.progress.value() > 300), "slider did not move"
    first = player.progress.value()
    assert wait_until(lambda: player.progress.value() > first + 300), "slider stopped moving"
    assert abs(player.progress.value() - player.media.position()) < 400   # tracks the real position
    assert wait_until(lambda: player.time_label.text() == "00:01", 3000)


def test_slider_reaches_the_end_of_the_song(player, songs):
    """When a song plays to its end, the slider ends up at (or next to) its maximum."""
    player.add_files(songs)
    player.play_index(0)                                   # alpha.wav, 1 s, 单曲播放
    assert wait_until(lambda: player.media.mediaStatus() == QMediaPlayer.EndOfMedia, 6000)
    process(100)
    assert player.progress.value() >= player.progress.maximum() - 150


def test_drag_handle_with_mouse_seeks_on_release(player, music_dir):
    """Drag the knob with the mouse to 3.5 s of a 5 s song:

    * while the button is held, the slider follows the mouse but playback does NOT
      jump yet (no stuttering sound while dragging);
    * on release, playback jumps to ~3.5 s and resumes (it was paused).
    """
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    slider = player.progress
    start = player.media.position()
    _, handle = _slider_rects(slider)
    y = handle.center().y()
    target = 3500

    _mouse(slider, QEvent.MouseButtonPress, handle.center(), Qt.LeftButton)
    assert slider.isSliderDown()
    for frac in (0.25, 0.5, 0.75, 1.0):                    # move in steps, like a real drag
        x = handle.center().x() + int((_x_for_value(slider, target) - handle.center().x()) * frac)
        _mouse(slider, QEvent.MouseMove, QPoint(x, y), Qt.LeftButton)
    assert abs(slider.value() - target) < 300, f"slider at {slider.value()}, expected ~{target}"
    process(100)
    assert abs(player.media.position() - start) < 100, "playback jumped before the mouse was released"

    _mouse(slider, QEvent.MouseButtonRelease, QPoint(_x_for_value(slider, target), y), Qt.NoButton)
    assert not slider.isSliderDown()
    assert wait_until(lambda: player.media.position() >= target - 300), \
        f"after release position is {player.media.position()}, expected ~{target}"
    assert wait_until(player.is_playing), "playback did not resume after the drag"


def _click_bar(player, value):
    """Left-click (press + release) the progress bar at the spot that stands for ``value`` ms."""
    slider = player.progress
    _, handle = _slider_rects(slider)
    spot = QPoint(_x_for_value(slider, value), handle.center().y())
    _mouse(slider, QEvent.MouseButtonPress, spot, Qt.LeftButton)
    _mouse(slider, QEvent.MouseButtonRelease, spot, Qt.NoButton)


def test_click_on_bar_jumps_to_mouse_position(player, music_dir):
    """Clicking the bar at the 4 s spot of a 5 s song jumps playback straight to ~4 s
    (not just 1/10 of the song toward it), and resumes the paused song."""
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    _click_bar(player, 4000)
    assert abs(player.progress.value() - 4000) < 250, f"knob at {player.progress.value()}"
    assert wait_until(lambda: abs(player.media.position() - 4000) < 300), \
        f"position {player.media.position()}, expected ~4000"
    assert wait_until(player.is_playing), "playback did not resume after the click"


def test_click_on_bar_left_of_knob_jumps_back(player, music_dir):
    """With the knob at ~4 s, clicking the 1 s spot jumps back to ~1 s."""
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    player.media.setPosition(4000)
    assert wait_until(lambda: player.progress.value() >= 3900)
    _click_bar(player, 1000)
    assert wait_until(lambda: abs(player.media.position() - 1000) < 300), \
        f"position {player.media.position()}, expected ~1000"


@pytest.mark.parametrize("value", [500, 2500, 4500])
def test_click_on_bar_anywhere(player, music_dir, value):
    """Clicks near the start, middle and end of the bar each land where clicked."""
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    _click_bar(player, value)
    assert wait_until(lambda: abs(player.media.position() - value) < 300), \
        f"clicked {value}, position {player.media.position()}"


def test_click_then_drag_continues_from_click_point(player, music_dir):
    """Press on the bar at 2 s (the knob jumps there and is grabbed), keep the button
    down and drag to 3 s, release: playback ends up at ~3 s, and doesn't move while
    the button is still held."""
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    slider = player.progress
    start = player.media.position()
    y = _slider_rects(slider)[1].center().y()
    _mouse(slider, QEvent.MouseButtonPress, QPoint(_x_for_value(slider, 2000), y), Qt.LeftButton)
    assert slider.isSliderDown(), "the knob was not grabbed after jumping"
    assert abs(slider.value() - 2000) < 250
    _mouse(slider, QEvent.MouseMove, QPoint(_x_for_value(slider, 3000), y), Qt.LeftButton)
    assert abs(slider.value() - 3000) < 250
    process(100)
    assert abs(player.media.position() - start) < 100, "playback jumped before release"
    _mouse(slider, QEvent.MouseButtonRelease, QPoint(_x_for_value(slider, 3000), y), Qt.NoButton)
    assert wait_until(lambda: abs(player.media.position() - 3000) < 300), \
        f"position {player.media.position()}, expected ~3000"


def test_click_on_knob_does_not_jump(player, music_dir):
    """Clicking the knob itself (without moving) leaves the position where it was."""
    _pause_at_start(player, make_wav(music_dir / "five_seconds.wav", 5.0))
    player.media.setPosition(2000)
    assert wait_until(lambda: player.progress.value() >= 1900)
    slider = player.progress
    knob = _slider_rects(slider)[1].center()
    _mouse(slider, QEvent.MouseButtonPress, knob, Qt.LeftButton)
    _mouse(slider, QEvent.MouseButtonRelease, knob, Qt.NoButton)
    process(200)
    assert abs(player.media.position() - 2000) < 300


def test_slider_resets_when_switching_songs(player, music_dir, songs):
    """After seeking far into a long song, switching to a short song resets the slider
    range to the new song's length and starts it near 0."""
    long_song = make_wav(music_dir / "five_seconds.wav", 5.0)
    player.add_files([str(long_song)] + songs)
    player.play_index(0)
    assert wait_until(lambda: player.progress.maximum() >= 4900)
    player.media.setPosition(4000)
    assert wait_until(lambda: player.progress.value() >= 3900)
    player.play_index(1)                                   # alpha.wav, 1 s
    assert wait_until(lambda: 0 < player.progress.maximum() <= 1100), \
        f"range still {player.progress.maximum()}"
    assert player.progress.value() <= 1100


# =============================================================== 8b. real-world MP3 files
# The other tests use WAV files. Real music libraries are mostly MP3s with ID3 tags,
# an embedded cover picture and a LAME "Info" header, built here by conftest.make_mp3
# (silent audio, no encoder needed).
#
# Regression: on Qt/PySide6 6.8.3, an MP3 with BOTH an embedded cover and a LAME
# header "played" forever at 0:00 with no sound (reported with a real song whose
# FFmpeg log showed "Stream #0:1: Video: mjpeg (attached pic)" and "Could not update
# timestamps for skipped samples"). Fixed by upgrading to PySide6 6.10.2.
def _wait_position(player, ms=500, timeout_ms=5000):
    """Wait until playback has really moved past ``ms`` milliseconds."""
    return wait_until(lambda: player.media.position() > ms, timeout_ms)


@pytest.fixture
def tagged_mp3(music_dir):
    """An MP3 like a typical store/iTunes file: ID3 title/artist/album, a 64x48 JPEG
    front cover and a LAME header (encoder delay 576 samples). 3 seconds long."""
    return str(make_mp3(music_dir / "Hacking test.mp3", 3, title="Test Song Title",
                        artist="テスト歌手", album="测试专辑",
                        cover=make_jpeg(64, 48), lame_header=True))


def test_mp3_with_cover_and_lame_header_plays(player, tagged_mp3):
    """The reported bug: an MP3 with an embedded cover AND a LAME header must actually
    play — the position moves and the time label leaves 00:00.

    If this fails with the position stuck at 0, the PySide6/Qt version has the
    Qt 6.8 FFmpeg bug: install the version from requirements.txt.
    """
    player.add_files([tagged_mp3])
    assert player.play_index(0)
    assert _wait_position(player), (
        "position stuck at 0: this Qt version can't play MP3s with cover art + LAME header")
    assert player.time_label.text() != "00:00" or player.media.position() < 1000
    assert player.is_playing()


def test_mp3_with_cover_reaches_the_end(player, tagged_mp3):
    """The same file plays to its end (3 s), so 单曲播放 stops and the Play button returns."""
    player.add_files([tagged_mp3])
    player.play_index(0)
    assert wait_until(lambda: player.media.mediaStatus() == QMediaPlayer.EndOfMedia, 10000)
    assert player.media.duration() >= 2900
    assert player.play_button.isVisibleTo(player)


@pytest.mark.parametrize("cover, lame", [(False, False), (True, False), (False, True)],
                         ids=["plain", "cover-only", "lame-only"])
def test_other_mp3_variants_play(player, music_dir, cover, lame):
    """MP3s without a cover, with only a cover, or with only a LAME header all play."""
    song = make_mp3(music_dir / "variant.mp3", 2, title="v",
                    cover=make_jpeg() if cover else None, lame_header=lame)
    player.add_files([str(song)])
    player.play_index(0)
    assert _wait_position(player)


def test_mp3_tags_are_shown(player, tagged_mp3):
    """Title, artist and album come from the ID3 tags (including Japanese/Chinese text),
    not from the file name."""
    player.add_files([tagged_mp3])
    player.play_index(0)
    assert wait_until(lambda: player.name_label.text() == "Test Song Title")
    assert player.artist_label.text() == "テスト歌手"
    assert player.album_label.text() == "测试专辑"
    assert player.lyric_window.text() == "Test Song Title"   # no .lrc: the bar shows the title


def test_mp3_embedded_cover_is_shown(player, tagged_mp3):
    """The cover stored inside the MP3 (64x48) replaces the default album picture."""
    player.add_files([tagged_mp3])
    player.play_index(0)
    assert wait_until(lambda: player.cover.pixmap().size().toTuple() == (64, 48)), \
        f"cover is {player.cover.pixmap().size().toTuple()}, expected the embedded 64x48 image"


def test_jpg_next_to_mp3_wins_over_embedded_cover(player, tagged_mp3):
    """A ``song.jpg`` beside the MP3 is used instead of the embedded cover."""
    QPixmap(37, 41).save(str(Path(tagged_mp3).with_suffix(".jpg")), "JPG")
    player.add_files([tagged_mp3])
    player.play_index(0)
    assert wait_until(lambda: player.name_label.text() == "Test Song Title")   # tags have arrived
    assert player.cover.pixmap().size().toTuple() == (37, 41)


# =============================================================== 9. song info / lyrics / cover
def test_metadata_fallbacks(player, songs):
    """A file without tags shows its file name, 未知音乐家 (unknown artist) and 未知专辑 (unknown album)."""
    player.add_files(songs)
    player.play_index(0)
    assert wait_until(lambda: player.artist_label.text() == "未知音乐家")
    assert player.name_label.text() == "alpha"
    assert player.album_label.text() == "未知专辑"


def test_metadata_ignored_when_nothing_selected(player):
    """A stray metadata signal with no current song leaves the placeholders alone."""
    player._on_metadata()
    assert player.name_label.text() == "Name"


def test_long_title_scrolls_short_title_does_not(player):
    """Titles over 28 characters scroll; short titles stand still."""
    player._set_title("x" * 40)
    assert player.name_label._timer.isActive()
    player._set_title("short")
    assert not player.name_label._timer.isActive() and player.name_label.text() == "short"


def test_lyrics_loaded_and_follow_position(player, songs):
    """测试歌曲.lrc is loaded; at 0.1 s the bar shows line 1, at 0.9 s line 2."""
    player.add_files(songs)
    player.play_index(1)
    assert player.lyrics
    player._on_position(100)
    assert player.lyric_window.text() == "第一行"
    player._on_position(900)
    assert player.lyric_window.text() == "第二行"


def test_lyrics_during_real_playback(player, songs):
    """With real audio, the lyric bar reaches the second line (0.8 s) by itself."""
    player.add_files(songs)
    player.play_index(1)
    assert wait_until(lambda: player.lyric_window.text() == "第二行", 4000)


def test_song_without_lyrics_shows_title(player, songs):
    """No .lrc file: the lyric bar shows the song title and keeps it while playing."""
    player.add_files(songs)
    player.play_index(0)
    assert not player.lyrics
    assert player.lyric_window.text() == "alpha"
    player._on_position(500)
    assert player.lyric_window.text() == "alpha"


def test_cover_from_sibling_jpg(player, songs):
    """``alpha.jpg`` next to ``alpha.wav`` is used as the cover (37x41 test image)."""
    QPixmap(37, 41).save(str(Path(songs[0]).with_suffix(".jpg")), "JPG")
    player.add_files(songs)
    player.play_index(0)
    assert player.cover.pixmap().size().toTuple() == (37, 41)


def test_default_cover_without_jpg(player, songs):
    """No cover image: the default album picture is shown."""
    player.add_files(songs)
    player.play_index(2)
    assert player.cover.pixmap().size() == player._default_cover.size()


def test_reset_info(player, songs):
    """Resetting (after clear/remove) brings back the placeholders and the app name in the lyric bar."""
    player.add_files(songs)
    player.play_index(1)
    player._reset_info()
    assert player.name_label.text() == "Name" and not player.lyrics
    assert player.lyric_window.text() == "冰点播放器" and player.time_label.text() == "00:00"


# =============================================================== 9b. online lyrics (lrclib.net)
# These use the ``lrclib`` fixture: a fake lrclib.net server on this computer, so no
# test needs (or uses) the internet.
ONLINE_LRC = "[00:00.00] 网上的第一行\n[00:30.00] 网上的第二行\n"


def test_lyric_button_searches_online_and_saves_lrc(player, songs, music_dir, lrclib):
    """alpha.wav has no alpha.lrc. Clicking the lyric button: the bar says 正在搜索歌词…,
    lrclib is asked for "alpha" (no artist: untagged WAV), the lyrics are shown, and
    saved as alpha.lrc so the next play needs no internet."""
    lrclib.results = [{"duration": 1.0, "syncedLyrics": ONLINE_LRC}]
    lrclib.delay = 0.3                       # long enough to see the "searching" text
    player.add_files(songs)
    player.play_index(0)
    player.lyric_button.click()
    assert player.lyric_window.text() == player_mod.LYRIC_SEARCHING
    assert wait_until(lambda: bool(player.lyrics))
    assert player.lyric_window.text() == "网上的第一行"
    assert lrclib.requests[0]["track_name"] == "alpha"
    assert "artist_name" not in lrclib.requests[0]
    assert (music_dir / "alpha.lrc").read_text(encoding="utf-8") == ONLINE_LRC


def test_lyric_button_no_search_when_lrc_exists(player, songs, lrclib):
    """测试歌曲.wav has its own .lrc: showing the bar never asks lrclib."""
    player.add_files(songs)
    player.play_index(1)
    player.lyric_button.click()
    process(300)
    assert lrclib.requests == [] and player.lyric_window.text() == "第一行"


def test_lyric_button_no_search_without_song(player, lrclib):
    """Nothing loaded: the bar opens with 冰点播放器, no search."""
    player.lyric_button.click()
    process(200)
    assert lrclib.requests == [] and player.lyric_window.text() == "冰点播放器"


def test_hiding_lyric_bar_does_not_search(player, songs, lrclib):
    """Only showing the bar searches: show (1 search), hide (none)."""
    player.add_files(songs)
    player.play_index(0)
    player.toggle_lyrics()
    assert wait_until(lambda: player.lyric_window.text() == player_mod.LYRIC_NOT_FOUND)
    player.toggle_lyrics()
    process(200)
    assert len(lrclib.requests) == 1


def test_lyrics_not_found_is_not_searched_again(player, songs, music_dir, lrclib):
    """lrclib has nothing: the bar says 没有找到歌词, no file is written, and showing
    the bar again for the same song doesn't ask again."""
    player.add_files(songs)
    player.play_index(0)
    player.toggle_lyrics()
    assert wait_until(lambda: player.lyric_window.text() == player_mod.LYRIC_NOT_FOUND)
    player.toggle_lyrics()
    player.toggle_lyrics()
    process(300)
    assert len(lrclib.requests) == 1
    assert not (music_dir / "alpha.lrc").exists()


@pytest.mark.parametrize("lrc, error, message", [
    (None, "", player_mod.LYRIC_NOT_FOUND), (None, "timed out", player_mod.LYRIC_FAILED)],
    ids=["not-found", "failed"])
def test_search_message_survives_late_tags(player, songs, lrc, error, message):
    """The song's tags can arrive after the search has ended. They must not replace
    the search message with the title (the message would flash and vanish).

    Input: search result delivered first, then the metadata callback. Expected: the
    message is still shown. A new song shows its own title again."""
    player.add_files(songs)
    player.play_index(0)
    player._lyric_message_shown = True       # as set by search_online_lyrics
    player._on_online_lyrics(songs[0], lrc, error)
    player._on_metadata()
    assert player.lyric_window.text() == message
    player.play_index(2)
    assert player.lyric_window.text() == "gamma"


def test_failed_lyric_search_is_retried(player, songs, lrclib):
    """The server fails (HTTP 500, like no internet): the bar says
    歌词搜索失败，请检查网络; the next time the bar is shown it tries again and works."""
    lrclib.status = 500
    player.add_files(songs)
    player.play_index(0)
    player.toggle_lyrics()
    assert wait_until(lambda: player.lyric_window.text() == player_mod.LYRIC_FAILED)
    lrclib.status = 200
    lrclib.results = [{"syncedLyrics": ONLINE_LRC}]
    player.toggle_lyrics()                   # hide
    player.toggle_lyrics()                   # show again -> retry
    assert wait_until(lambda: bool(player.lyrics))
    assert len(lrclib.requests) == 2


def test_late_lyrics_for_previous_song_are_ignored(player, songs, music_dir, lrclib):
    """The answer for alpha arrives after the user switched to gamma: it is thrown
    away (gamma doesn't get alpha's lyrics, no alpha.lrc is written)."""
    lrclib.results = [{"syncedLyrics": ONLINE_LRC}]
    lrclib.delay = 0.5
    player.add_files(songs)
    player.play_index(0)
    player.toggle_lyrics()
    player.play_index(2)                     # switch before the answer arrives
    process(1200)
    assert len(lrclib.requests) == 1
    assert not player.lyrics
    assert not (music_dir / "alpha.lrc").exists()
    assert player.lyric_window.text() != "网上的第一行"


def test_online_search_uses_mp3_tags(player, tagged_mp3, lrclib):
    """A tagged MP3: the search uses its title and artist tags, not the file name."""
    player.add_files([tagged_mp3])
    player.play_index(0)
    assert wait_until(lambda: player.artist_label.text() == "テスト歌手")
    player.toggle_lyrics()
    assert wait_until(lambda: len(lrclib.requests) == 1)
    assert lrclib.requests[0]["track_name"] == "Test Song Title"
    assert lrclib.requests[0]["artist_name"] == "テスト歌手"


def test_online_lyrics_from_mini_window_menu(player, songs, lrclib):
    """The mini window's 桌面歌词 menu item shows the bar the same way, so it also searches."""
    lrclib.results = [{"syncedLyrics": ONLINE_LRC}]
    player.add_files(songs)
    player.play_index(2)
    action = next(a for a in player.mini_window.menu.actions() if a.text() == "桌面歌词")
    action.trigger()
    assert wait_until(lambda: bool(player.lyrics))
    assert lrclib.requests[0]["track_name"] == "gamma"

# =============================================================== 10. other windows & buttons
def test_toggle_lyrics(player):
    """The lyric button shows the lyric bar; pressing again hides it."""
    assert player.lyric_window.isHidden()
    player.lyric_button.click()
    assert player.lyric_window.isVisible()
    player.toggle_lyrics()
    assert player.lyric_window.isHidden()


def test_mini_mode_and_back(player):
    """Mini-mode button hides the main window and shows the mini window; and back."""
    player.show()
    player.mini_mode_button.click()
    assert player.isHidden() and player.mini_window.isVisible()
    player.bring_to_front()
    assert player.isVisible() and player.mini_window.isHidden()


def test_logo_opens_about(player):
    """Clicking the logo (top left) opens the About box."""
    player.show()
    player.logo_button.click()
    assert player.about_dialog.isVisible()
    player.about_dialog.close()


def test_minimize_button(player):
    """The minimise button minimises the window to the taskbar."""
    player.show()
    player.minimize_button.click()
    assert player.windowState() & Qt.WindowMinimized


def test_add_button_opens_file_dialog(player):
    """The "+" button opens the add-songs dialog (mocked, answered with Cancel)."""
    with patch.object(player_mod.QFileDialog, "getOpenFileNames", return_value=([], "")) as dlg:
        player.add_button.click()
    dlg.assert_called_once()


def test_paint(player):
    """The window can be drawn (background image and all widgets) without errors."""
    assert not player.grab().isNull()


# =============================================================== 11. open with / drag & drop
def test_open_and_play(player, songs):
    """"Open with IcePlayer" on a file adds and plays it; opening it again doesn't duplicate it."""
    player.open_and_play(songs[2])
    wait_playing(player, 0)
    player.open_and_play(songs[2])
    assert len(player.files) == 1


@pytest.mark.parametrize("arg", ["", "Z:/does/not/exist.mp3"])
def test_open_and_play_ignores_bad_paths(player, arg):
    """An empty or non-existent path from the command line is ignored."""
    player.open_and_play(arg)
    assert player.files == []


def _mime(paths):
    """The data Windows Explorer attaches to a drag: a list of file URLs."""
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return m


# Keep the QMimeData in a variable: the event does not own it, and a temporary would be
# garbage-collected while the event still points at it.
def test_drag_enter_accepts_files(player, songs):
    """Dragging files over the window is accepted (the cursor shows "copy")."""
    mime = _mime(songs)
    ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    ev.ignore()
    player.dragEnterEvent(ev)
    assert ev.isAccepted()


def test_drag_enter_rejects_text(player):
    """Dragging plain text (not files) over the window is refused."""
    m = QMimeData()
    m.setText("hello")
    ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)
    ev.ignore()
    player.dragEnterEvent(ev)
    assert not ev.isAccepted()


def test_drop_files_and_folder(player, music_dir, tmp_path):
    """Dropping a song, a .txt file and a folder adds the song plus the folder's songs, skipping the .txt."""
    other = tmp_path / "other"
    other.mkdir()
    make_wav(other / "delta.wav")
    drop = [music_dir / "alpha.wav", music_dir / "notes.txt", other]
    mime = _mime(drop)
    player.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert names(player) == ["alpha", "delta"]


def test_drop_nothing_useful(player, music_dir):
    """Dropping only non-audio files adds nothing."""
    mime = _mime([music_dir / "notes.txt", music_dir / "测试歌曲.lrc"])
    player.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert player.files == []


# =============================================================== 12. closing
def test_close_saves_state_and_closes_windows(player, songs):
    """Closing saves playlist + volume 55 + 随机播放 (3), and closes the lyric and mini windows."""
    player.add_files(songs)
    player.set_volume(55)
    player.set_play_mode(PlayMode.RANDOM)
    player.toggle_lyrics()
    player.show()
    player.close()
    saved = json.loads(player.state_file.read_text(encoding="utf-8"))
    assert saved == {"files": player.files, "volume": 55, "mode": 3}
    assert player.lyric_window.isHidden() and player.mini_window.isHidden()


def test_close_button(player):
    """The X button closes the main window."""
    player.show()
    player.close_button.click()
    assert player.isHidden()
