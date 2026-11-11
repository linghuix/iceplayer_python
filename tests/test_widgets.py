"""Tests for iceplayer/widgets.py — the small building blocks every window uses.

What is tested
--------------
1. Skin images     ``res()`` / ``pixmap()`` find every image the windows load, and the
                   window backgrounds have the sizes the windows are drawn at.
2. ``IceButton``   The flat image buttons: swap to the highlight image on mouse hover,
                   back on leave, no highlight when disabled, emit ``clicked``.
3. ``ScrollingLabel``
                   The song title that scrolls (marquee) when it is too long: steps one
                   character at a time, wraps around, stops, restarts from the start.
4. ``VolumeButton``  The speaker button + popup slider: get/set, 0..100 limits, the
                   ``volume_changed`` signal, and that a click opens the popup.
5. ``NoFocusDelegate``
                   Removes the dotted focus rectangle from playlist rows.
6. ``DraggableMixin``
                   Dragging a frameless window with the left mouse button moves it.

How it is tested
----------------
Widgets are created off-screen (see conftest.py). Mouse hover/drag is simulated by
building Qt event objects (QEnterEvent, QMouseEvent) and passing them to the
widget's event handler, exactly what Qt would do for a real mouse. Icons are
compared by ``cacheKey()``, a number that changes when a different image is set.

How to run
----------
    python -m pytest tests/test_widgets.py -v
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QEvent, QModelIndex, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

from conftest import process, wait_until
from iceplayer.widgets import (RES_DIR, SLIDER_STYLE, DraggableMixin, IceButton,
                               NoFocusDelegate, ScrollingLabel, SeekSlider, VolumeButton,
                               pixmap, res)

# Every image file the app loads. Buttons come in pairs: normal + "_hover".
ALL_IMAGES = [
    "about.png", "background.png", "default_album.jpg", "lrc_background.png", "mini.png",
    "logo-16.png", "logo-32.png", "logo-48.png", "mini_play.png", "mini_pause.png", "mini_next.png",
] + [f"{n}{s}.png" for n in ("add", "close", "logo", "lyric_btn", "mini_mode", "minimize", "mode",
                             "next", "pause", "play", "prev", "volume") for s in ("", "_hover")]


# =============================================================== skin images
def test_res_points_into_resources_dir():
    """``res("play.png")`` resolves to iceplayer/resources/play.png."""
    assert Path(res("play.png")) == RES_DIR / "play.png"


@pytest.mark.parametrize("name", ALL_IMAGES)
def test_every_image_loads(qapp, name):
    """Each image exists and is a valid picture (one test per file; a missing one is named in the failure)."""
    assert not pixmap(name).isNull(), name


def test_background_sizes_match_windows(qapp):
    """Window backgrounds match the fixed window sizes, so nothing is cut off or stretched."""
    assert pixmap("background.png").size().toTuple() == (400, 600)
    assert pixmap("lrc_background.png").size().toTuple() == (800, 80)
    assert pixmap("mini.png").size().toTuple() == (100, 100)
    assert pixmap("about.png").size().toTuple() == (300, 400)


# =============================================================== IceButton
def _icon_key(button):
    """A number identifying the button's current image (changes when the image changes)."""
    return button.icon().cacheKey()


def test_ice_button_hover_swaps_icon(qapp):
    """Mouse enters -> highlight image; mouse leaves -> normal image again."""
    b = IceButton(None, "play.png", "play_hover.png", (60, 60))
    normal = _icon_key(b)
    b.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert _icon_key(b) != normal
    b.leaveEvent(QEvent(QEvent.Leave))
    assert _icon_key(b) == normal


def test_ice_button_disabled_does_not_highlight(qapp):
    """A disabled button ignores hover."""
    b = IceButton(None, "play.png", "play_hover.png", (60, 60))
    normal = _icon_key(b)
    b.setEnabled(False)
    b.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert _icon_key(b) == normal


def test_ice_button_without_hover_icon(qapp):
    """Buttons with no hover image (the mini-window ones) keep their image on hover."""
    b = IceButton(None, "mini_play.png", None, (34, 34))
    normal = _icon_key(b)
    b.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert _icon_key(b) == normal


def test_ice_button_appearance(qapp):
    """Flat (no button frame), never takes keyboard focus, icon at the requested size."""
    b = IceButton(None, "add.png", "add_hover.png", (25, 25))
    assert b.isFlat()
    assert b.focusPolicy() == Qt.NoFocus
    assert b.iconSize().toTuple() == (25, 25)


def test_ice_button_click_signal(qapp):
    """A click emits ``clicked`` exactly once."""
    b = IceButton(None, "add.png", "add_hover.png", (25, 25))
    hits = []
    b.clicked.connect(lambda: hits.append(1))
    b.click()
    assert hits == [1]


# =============================================================== ScrollingLabel
def test_scrolling_label_steps_and_wraps(qapp):
    """Each tick drops one leading character; after the text is gone it starts over.

    How: a 10 s interval keeps the real timer from firing, and ``_tick()`` is called by hand.
    """
    label = ScrollingLabel()
    label.set_scrolling_text("abc", interval_ms=10000)
    assert label.text() == "abc"
    seen = []
    for _ in range(5):
        label._tick()
        seen.append(label.text())
    assert seen == ["abc", "bc", "c", "", "abc"]


def test_scrolling_label_timer_runs_and_stops(qapp):
    """With a 10 ms timer the text moves by itself; after ``stop()`` it stays put.

    How: wait (up to 2 s) until the timer has ticked at least twice, so a busy PC
    can't make the test fail just because the timer fired late.
    """
    label = ScrollingLabel()
    label.set_scrolling_text("abcdef", interval_ms=10)
    assert wait_until(lambda: label._pos > 1, 2000)
    assert label.text() != "abcdef"
    label.stop()
    assert not label._timer.isActive()
    frozen = label.text()
    process(50)
    assert label.text() == frozen


def test_scrolling_label_restart_resets_position(qapp):
    """Setting a new title starts scrolling from its first character."""
    label = ScrollingLabel()
    label.set_scrolling_text("hello", 10000)
    label._tick(); label._tick(); label._tick()
    label.set_scrolling_text("world", 10000)
    assert label._pos == 0 and label.text() == "world"


# =============================================================== SeekSlider
@pytest.fixture
def seek_slider(qapp):
    """A 0-10000 SeekSlider, 290x20 with the player's style sheet (like the progress bar)."""
    s = SeekSlider(Qt.Horizontal)
    s.setStyleSheet(SLIDER_STYLE)
    s.setRange(0, 10000)
    s.resize(290, 20)
    s.show()
    yield s
    s.close()


def _slider_mouse(slider, kind, x, buttons=Qt.LeftButton, button=Qt.LeftButton):
    """Send a mouse event at horizontal position ``x`` (vertical centre) to the slider."""
    pos = QPoint(x, slider.height() // 2)
    ev = QMouseEvent(kind, QPointF(pos), QPointF(slider.mapToGlobal(pos)), button, buttons, Qt.NoModifier)
    slider.event(ev)


def _x_of(slider, value):
    """The pixel where ``value_at`` gives ``value`` (searched, so it matches any style)."""
    return min(range(slider.width()), key=lambda x: abs(slider.value_at(x) - value))


def test_seek_slider_value_at_ends_and_middle(seek_slider):
    """``value_at`` maps the left end to 0, the right end to 10000, increasing in between;
    positions past the ends are limited to the range."""
    s = seek_slider
    assert s.value_at(-50) == 0 and s.value_at(0) == 0
    assert s.value_at(s.width() + 50) == 10000 and s.value_at(s.width() - 1) == 10000
    values = [s.value_at(x) for x in range(0, s.width(), 10)]
    assert values == sorted(values)
    assert 4000 < s.value_at(s.width() // 2) < 6000


@pytest.mark.parametrize("target", [1500, 5000, 9000])
def test_seek_slider_click_jumps_to_mouse(seek_slider, target):
    """A left-click on the bar moves the knob straight to the clicked value (not a
    1/10 page step), and the knob is grabbed (``isSliderDown``) until release."""
    s = seek_slider
    released = []
    s.sliderReleased.connect(lambda: released.append(s.value()))
    x = _x_of(s, target)
    _slider_mouse(s, QEvent.MouseButtonPress, x)
    assert abs(s.value() - target) < 100, f"value {s.value()}, expected ~{target}"
    assert s.isSliderDown()
    _slider_mouse(s, QEvent.MouseButtonRelease, x, buttons=Qt.NoButton)
    assert not s.isSliderDown()
    assert len(released) == 1 and abs(released[0] - target) < 100   # what the player seeks to


def test_seek_slider_click_on_knob_does_not_move_it(seek_slider):
    """A click on the knob itself grabs it without changing the value."""
    s = seek_slider
    s.setValue(5000)
    _slider_mouse(s, QEvent.MouseButtonPress, _x_of(s, 5000))
    assert s.value() == 5000 and s.isSliderDown()


def test_seek_slider_right_click_does_nothing(seek_slider):
    """Right-clicks don't move the knob."""
    s = seek_slider
    _slider_mouse(s, QEvent.MouseButtonPress, _x_of(s, 8000), Qt.RightButton, Qt.RightButton)
    assert s.value() == 0


def test_seek_slider_disabled_ignores_clicks(seek_slider):
    """A disabled slider (no song loaded) ignores clicks."""
    s = seek_slider
    s.setEnabled(False)
    _slider_mouse(s, QEvent.MouseButtonPress, _x_of(s, 8000))
    assert s.value() == 0


def test_seek_slider_click_then_drag(seek_slider):
    """After the click-jump the user holds the knob: dragging moves it on from there."""
    s = seek_slider
    _slider_mouse(s, QEvent.MouseButtonPress, _x_of(s, 3000))
    _slider_mouse(s, QEvent.MouseMove, _x_of(s, 7000))
    assert abs(s.value() - 7000) < 150
    _slider_mouse(s, QEvent.MouseButtonRelease, _x_of(s, 7000), buttons=Qt.NoButton)
    assert abs(s.value() - 7000) < 150


# =============================================================== VolumeButton
def test_volume_button_get_set_and_signal(qapp):
    """``set_volume`` updates the value and emits ``volume_changed`` once with it."""
    v = VolumeButton()
    got = []
    v.volume_changed.connect(got.append)
    v.set_volume(37)
    assert v.volume() == 37 and got == [37]


@pytest.mark.parametrize("value, expected", [(-10, 0), (150, 100)])
def test_volume_button_clamps(qapp, value, expected):
    """Out-of-range volumes are limited to 0..100."""
    v = VolumeButton()
    v.set_volume(value)
    assert v.volume() == expected


def test_volume_button_click_opens_menu(qapp):
    """Clicking the speaker opens the slider popup (``menu.exec`` mocked so it doesn't block)."""
    v = VolumeButton()
    with patch.object(v.menu, "exec") as exec_:
        v.button.click()
    exec_.assert_called_once()


# =============================================================== NoFocusDelegate
def test_no_focus_delegate_strips_focus_flag(qapp):
    """Before drawing a row, the "has focus" flag is removed but "selected" is kept.

    How: the normal Qt drawing function is replaced by a spy that records the flags
    it receives, then one row is drawn into a small image.
    """
    seen = {}

    def spy(self, painter, option, index):
        seen["state"] = option.state

    option = QStyleOptionViewItem()
    option.state = QStyle.State_HasFocus | QStyle.State_Selected
    img = QImage(10, 10, QImage.Format_ARGB32)
    painter = QPainter(img)
    with patch("iceplayer.widgets.QStyledItemDelegate.paint", spy):
        NoFocusDelegate().paint(painter, option, QModelIndex())
    painter.end()
    assert not (seen["state"] & QStyle.State_HasFocus)
    assert seen["state"] & QStyle.State_Selected


# =============================================================== DraggableMixin
class _DragWidget(DraggableMixin, QWidget):
    """A bare window with only the dragging behaviour, to test it in isolation."""


def _mouse(kind, pos, global_pos, buttons=Qt.LeftButton, button=Qt.LeftButton):
    """Build a mouse event at ``pos`` inside the window / ``global_pos`` on the screen."""
    return QMouseEvent(kind, QPointF(pos), QPointF(global_pos), button, buttons, Qt.NoModifier)


def test_drag_moves_window(qapp):
    """Press left button, move the mouse by (40, 20): the window moves by (40, 20). Release ends the drag."""
    w = _DragWidget()
    w.resize(50, 50)
    w.move(100, 100)
    w.show()
    start = w.frameGeometry().topLeft()
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(5, 5), start + QPoint(5, 5)))
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(5, 5), start + QPoint(45, 25)))
    assert w.frameGeometry().topLeft() == start + QPoint(40, 20)
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, QPoint(5, 5), start, Qt.NoButton))
    assert w._drag_offset is None
    w.close()


def test_right_button_does_not_drag(qapp):
    """Right-button drags (used for context menus) never move the window."""
    w = _DragWidget()
    w.move(100, 100)
    w.show()
    start = w.frameGeometry().topLeft()
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, QPoint(5, 5), start, Qt.RightButton, Qt.RightButton))
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(5, 5), start + QPoint(30, 30), Qt.RightButton, Qt.NoButton))
    assert w.frameGeometry().topLeft() == start
    w.close()


def test_move_without_press_does_nothing(qapp):
    """Just moving the mouse over the window (no button held) doesn't move it."""
    w = _DragWidget()
    w.move(100, 100)
    w.show()
    start = w.frameGeometry().topLeft()
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, QPoint(5, 5), start + QPoint(30, 30)))
    assert w.frameGeometry().topLeft() == start
    w.close()
