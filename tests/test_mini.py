"""Tests for iceplayer/mini.py — the About dialog and the mini-mode window.

What is tested
--------------
1. ``AboutDialog``  The 300x400 "about" box opened by clicking the logo: size, modal,
                    frameless, painting, and its close (X) button.
2. ``MiniWindow``   The 100x100 floating round button shown in mini mode:
                    - its play / pause / next buttons control the main player,
                    - its right-click menu (返回主界面, volume slider, 下一曲, 上一曲,
                      the four play modes, 桌面歌词, 退出) does what each item says,
                    - it stays in sync with the main window (play state, mode, volume),
                    - double-click returns to the main window.

How it is tested
----------------
Most tests use the ``player`` fixture (see conftest.py), because the mini window only
exists as part of a player and acts on it. Menu items are tested by calling
``action.trigger()``, which runs exactly what a click on that item runs, without
opening the real menu. Play/pause use real audio, so those tests wait with
``wait_until`` for playback to start or stop.

How to run
----------
    python -m pytest tests/test_mini.py -v
"""
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent

from conftest import wait_until
from iceplayer.mini import AboutDialog
from iceplayer.playlist import PlayMode
from iceplayer.widgets import IceButton


# =============================================================== AboutDialog
def test_about_dialog(qapp):
    """About box: 300x400, modal (blocks the main window), frameless, paints its image."""
    d = AboutDialog()
    assert d.size().toTuple() == (300, 400)
    assert d.isModal()
    assert d.windowFlags() & Qt.FramelessWindowHint
    assert not d.grab().isNull()


def test_about_close_button(qapp):
    """Clicking the X button (its only IceButton) closes the About box."""
    d = AboutDialog()
    d.show()
    d.findChildren(IceButton)[0].click()
    assert d.isHidden()


# =============================================================== MiniWindow
def menu_action(mini, text):
    """Find a right-click menu item of the mini window by its label."""
    return [a for a in mini.menu.actions() if a.text() == text][0]


def test_mini_window_basics(player):
    """Mini window: 100x100, always on top, paints its round background."""
    m = player.mini_window
    assert m.size().toTuple() == (100, 100)
    assert m.windowFlags() & Qt.WindowStaysOnTopHint
    assert not m.grab().isNull()


def test_mini_menu_contents(player):
    """The right-click menu has all items in the original order (the volume slider has no label)."""
    texts = [a.text() for a in player.mini_window.menu.actions() if a.text()]
    assert texts == ["返回主界面", "下一曲", "上一曲", "单曲播放", "列表循环", "单曲循环",
                     "随机播放", "桌面歌词", "退出"]


def test_show_mini_syncs_state(player):
    """Entering mini mode copies the main window's volume, play mode and play/pause state."""
    player.set_volume(42)
    player.set_play_mode(PlayMode.SINGLE_LOOP)
    player.enter_mini_mode()
    m = player.mini_window
    assert m.vol_slider.value() == 42
    assert m.mode_group.checkedAction().text() == "单曲循环"
    assert m.play_button.isVisibleTo(m) and not m.pause_button.isVisibleTo(m)


def test_mini_follows_mode_changes_from_main(player):
    """Changing the mode in the main window ticks the same mode in the mini menu."""
    player.set_play_mode(PlayMode.RANDOM)
    assert player.mini_window.mode_group.checkedAction().text() == "随机播放"


def test_mini_mode_menu_changes_player_mode(player):
    """Choosing 列表循环 in the mini menu changes the player's mode and the main menu's tick."""
    menu_action(player.mini_window, "列表循环").trigger()
    assert player.play_mode == PlayMode.LIST_LOOP
    assert player.mode_group.checkedAction().text() == "列表循环"


def test_mini_volume_slider_sets_player_volume(player):
    """Moving the mini menu's volume slider changes the real audio volume (15 -> 0.15)."""
    player.mini_window.vol_slider.setValue(15)
    assert player.volume_button.volume() == 15
    assert abs(player.audio.volume() - 0.15) < 0.01


def test_mini_play_pause_next(player, songs):
    """Mini play starts music, pause (with fade) stops it, the small arrow plays the next song.

    How: click the real buttons and wait for the player state; also checks the mini
    window swaps its play/pause icon to match.
    """
    player.add_files(songs)
    player.set_play_mode(PlayMode.LIST_LOOP)
    player.enter_mini_mode()
    m = player.mini_window
    m.play_button.click()
    assert wait_until(player.is_playing)
    assert m.pause_button.isVisibleTo(m) and not m.play_button.isVisibleTo(m)
    m.pause_button.click()
    assert wait_until(lambda: not player.is_playing(), 3000)
    assert m.play_button.isVisibleTo(m)
    start = player.current_index
    # The third button (neither play nor pause) is the "next" arrow.
    [b for b in m.findChildren(type(m.play_button)) if b not in (m.play_button, m.pause_button)][0].click()
    assert player.current_index == (start + 1) % 3


def test_mini_menu_next_previous(player, songs):
    """下一曲 / 上一曲 in the mini menu move to the next / previous song."""
    player.add_files(songs)
    player.play_index(1)
    menu_action(player.mini_window, "下一曲").trigger()
    assert player.current_index == 2
    menu_action(player.mini_window, "上一曲").trigger()
    assert player.current_index == 1


def test_mini_menu_lyrics(player):
    """桌面歌词 shows the desktop lyric bar."""
    menu_action(player.mini_window, "桌面歌词").trigger()
    assert player.lyric_window.isVisible()


def test_mini_back_to_main(player):
    """返回主界面 shows the main window again and hides the mini window."""
    player.show()
    player.enter_mini_mode()
    menu_action(player.mini_window, "返回主界面").trigger()
    assert player.isVisible() and player.mini_window.isHidden()


def test_mini_double_click_back_to_main(player):
    """Double-clicking the mini window also returns to the main window.

    How: build a double-click mouse event and hand it to the window directly.
    """
    player.enter_mini_mode()
    ev = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(50, 50), QPointF(50, 50),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    player.mini_window.mouseDoubleClickEvent(ev)
    assert player.isVisible() and player.mini_window.isHidden()


def test_mini_exit_closes_player(player, songs):
    """退出 closes the whole player, and the playlist is saved on the way out."""
    player.add_files(songs)
    player.enter_mini_mode()
    menu_action(player.mini_window, "退出").trigger()
    assert player.mini_window.isHidden()
    assert player.state_file.exists()


def test_mini_context_menu_opens(player):
    """Right-clicking the mini window opens its menu.

    How: ``menu.exec`` is replaced by a mock that records the call instead of
    showing a real menu that would wait for a click.
    """
    with patch.object(player.mini_window.menu, "exec") as exec_:
        player.mini_window.contextMenuEvent(
            QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), QPoint(5, 5)))
    exec_.assert_called_once()


def test_mini_set_playing(player):
    """``set_playing(True/False)`` shows the pause or the play icon, never both."""
    m = player.mini_window
    m.set_playing(True)
    assert m.pause_button.isVisibleTo(m) and not m.play_button.isVisibleTo(m)
    m.set_playing(False)
    assert m.play_button.isVisibleTo(m) and not m.pause_button.isVisibleTo(m)
