"""Reusable widgets and shared styling used by all windows.

* ``res`` / ``pixmap``   locate and load skin images from ``iceplayer/resources``.
* ``IceButton``          flat image button that highlights on mouse hover.
* ``ScrollingLabel``     label that scrolls (marquee) long song titles.
* ``SeekSlider``         slider whose knob jumps straight to where you click.
* ``VolumeButton``       speaker icon with a popup volume slider.
* ``PlaylistTable``      table that paints its background/stripes with an exact opacity.
* ``NoFocusDelegate``    hides the dotted focus box on playlist rows.
* ``DraggableMixin``     lets a frameless window be moved by dragging it.
* ``SLIDER_STYLE`` / ``MENU_STYLE`` / ``TRANSPARENT_BUTTON``  Qt style sheets.
"""
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QLabel, QMenu, QPushButton, QSlider, QStyle,
                               QStyledItemDelegate, QStyleOptionSlider, QTableWidget,
                               QWidget, QWidgetAction)

#: Folder holding the skin images (also bundled into the .exe by IcePlayer.spec).
RES_DIR = Path(__file__).resolve().parent / "resources"
#: Font used throughout the UI (微软雅黑, as in the original).
FONT_FAMILY = "Microsoft YaHei"

#: Blue thin slider used for the progress bar and volume sliders.
SLIDER_STYLE = (
    "QSlider::groove:horizontal{border:0px;height:4px;}"
    "QSlider::sub-page:horizontal{background:#0096ff;}"
    "QSlider::add-page:horizontal{background:lightgray;}"
    "QSlider::handle:horizontal{background:white;width:10px;border:#51b5fb 10px;"
    "border-radius:5px;margin:-3px 0px -3px 0px;}"
)

#: White popup menu with blue highlighted items.
MENU_STYLE = (
    "QMenu{padding:5px;background:white;border:1px solid gray;}"
    "QMenu::item{padding:0px 40px 0px 30px;height:25px;}"
    "QMenu::item:selected:enabled{background:#0096ff;color:white;}"
    "QMenu::item:selected:!enabled{background:transparent;}"
    "QMenu::separator{height:1px;background:lightgray;margin:5px 0px 5px 0px;}"
)

#: Button with no background or border, so only its image shows.
TRANSPARENT_BUTTON = "QPushButton{background:transparent;border:0px;}"


def res(name: str) -> str:
    """Build the full path of a skin image.

    Args:
        name: File name inside ``resources``, e.g. ``"play.png"``.

    Returns:
        The absolute path as a string (works both from source and from the .exe).
    """
    return str(RES_DIR / name)


def pixmap(name: str) -> QPixmap:
    """Load a skin image.

    Args:
        name: File name inside ``resources``, e.g. ``"background.png"``.

    Returns:
        The image as a QPixmap (``isNull()`` is True if the file is missing).
    """
    return QPixmap(res(name))


class IceButton(QPushButton):
    """Flat image button that swaps to a highlight image while the mouse is over it."""

    def __init__(self, parent, icon: str, hover_icon: str | None, icon_size: QSize | tuple):
        """Create the button.

        Args:
            parent:     Widget the button lives in (or None).
            icon:       Normal image file name, e.g. ``"play.png"``.
            hover_icon: Highlight image file name, e.g. ``"play_hover.png"``;
                        None keeps the normal image on hover.
            icon_size:  Image size as ``(width, height)`` or a QSize.
        """
        super().__init__(parent)
        self._normal = QIcon(res(icon))
        self._hover = QIcon(res(hover_icon)) if hover_icon else self._normal
        self.setIcon(self._normal)
        self.setIconSize(QSize(*icon_size) if isinstance(icon_size, tuple) else icon_size)
        self.setFlat(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(TRANSPARENT_BUTTON)

    def enterEvent(self, event):
        """Qt callback when the mouse enters: show the highlight image (if enabled).

        Args:
            event: Qt enter event, passed on to QPushButton.
        """
        if self.isEnabled():
            self.setIcon(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Qt callback when the mouse leaves: back to the normal image.

        Args:
            event: Qt leave event, passed on to QPushButton.
        """
        self.setIcon(self._normal)
        super().leaveEvent(event)


class ScrollingLabel(QLabel):
    """Label that marquee-scrolls long text by dropping one leading character per tick
    (IceLabel in the C++ version). Behaves like a normal QLabel until
    ``set_scrolling_text`` is called."""

    def __init__(self, parent=None):
        """Create an empty, non-scrolling label.

        Args:
            parent: Widget the label lives in (or None).
        """
        super().__init__(parent)
        self._text = ""
        self._pos = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_scrolling_text(self, text: str, interval_ms: int = 500):
        """Show ``text`` and start scrolling it from the first character.

        Args:
            text:        Full text to scroll.
            interval_ms: Time between scroll steps in milliseconds.
        """
        self._text, self._pos = text, 0
        self.setText(text)
        self._timer.start(interval_ms)

    def stop(self):
        """Stop scrolling; the label keeps whatever text it shows now."""
        self._timer.stop()

    def _tick(self):
        """Timer callback: show the text from the next character on; wrap to the start
        after the text has scrolled away completely."""
        if self._pos > len(self._text):
            self._pos = 0
        self.setText(self._text[self._pos:])
        self._pos += 1


class SeekSlider(QSlider):
    """Horizontal slider where a left-click on the bar moves the knob straight to the
    mouse (a plain QSlider only steps a "page" toward the click).

    After jumping, the click is handed to QSlider as if it had landed on the knob, so
    the user is now holding the knob: releasing emits ``sliderReleased`` (the player
    seeks then), and keeping the button down drags on from the new spot.
    """

    def value_at(self, x: int) -> int:
        """Convert a horizontal pixel position into a slider value.

        Args:
            x: Position inside the slider, in pixels (0 = left edge of the widget).

        Returns:
            The value whose knob centre would sit at ``x``, limited to
            ``minimum()``..``maximum()`` (clicks past either end give the end value).
        """
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        pos = x - groove.x() - handle.width() // 2
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos, max(1, span))

    def _handle_rect(self):
        """Returns: the knob's current rectangle, in pixels inside the slider."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)

    def mousePressEvent(self, event):
        """Qt callback: a left-click away from the knob moves the knob to the mouse first.

        Args:
            event: Qt mouse event. Clicks on the knob itself, other buttons and clicks
                   on a disabled slider behave exactly like a normal QSlider.
        """
        point = event.position().toPoint()
        if (event.button() == Qt.LeftButton and self.isEnabled()
                and not self._handle_rect().contains(point)):
            self.setSliderPosition(self.value_at(point.x()))
        super().mousePressEvent(event)   # the knob is now under the mouse: start dragging it


class VolumeButton(QWidget):
    """Speaker icon that pops up a horizontal 0-100 volume slider when clicked.

    Signals:
        volume_changed(int): emitted with the new volume 0-100 whenever it changes.
    """

    volume_changed = Signal(int)

    def __init__(self, parent=None):
        """Create the speaker button, the slider (0-100) and its popup menu.

        Args:
            parent: Widget the button lives in (or None).
        """
        super().__init__(parent)
        self.button = IceButton(self, "volume.png", "volume_hover.png", (25, 25))
        self.button.setGeometry(0, 0, 25, 25)
        self.button.clicked.connect(self._popup)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedSize(100, 25)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.valueChanged.connect(self.volume_changed)

        self.menu = QMenu(self)
        self.menu.setStyleSheet("QMenu{padding:4px 8px;background:white;border:1px solid lightgray;}")
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(self.slider)
        self.menu.addAction(action)

    def _popup(self):
        """Show the slider popup just above the speaker icon (blocks until it closes)."""
        self.menu.exec(self.button.mapToGlobal(QPoint(-35, -32)))

    def volume(self) -> int:
        """Returns: the current volume, 0-100."""
        return self.slider.value()

    def set_volume(self, value: int):
        """Set the volume; emits ``volume_changed`` if it changed.

        Args:
            value: New volume; values outside 0-100 are clamped by the slider.
        """
        self.slider.setValue(value)


class PlaylistTable(QTableWidget):
    """Playlist table that paints its own background, row stripes included, with an
    exact, explicit opacity.

    Normally the background comes from several layers (widget, scroll area, item
    brushes, style sheet, system palette); translucent layers stack up and the result
    depends on the computer's theme and style. Here every background pixel is painted
    once, with ``CompositionMode_Source`` (it *replaces* what is underneath instead of
    blending with it), so a row set to alpha 180 really is alpha 180 on screen. Text,
    selection highlight and focus handling are then drawn normally on top.
    """

    def __init__(self, rows: int, columns: int, parent=None):
        """Create the table.

        Args:
            rows / columns: Initial size (like QTableWidget).
            parent:         Parent widget.
        """
        super().__init__(rows, columns, parent)
        self._odd = QColor(255, 255, 255)
        self._even = QColor(255, 255, 255)
        for widget in (self, self.viewport()):
            widget.setAutoFillBackground(False)      # only paintEvent paints the background
            widget.setAttribute(Qt.WA_TranslucentBackground, False)

    def set_row_colors(self, odd: QColor, even: QColor):
        """Set the background colours (their alpha is the opacity) and repaint.

        Args:
            odd:  Colour of rows 1, 3, 5... and of the empty area below the last row.
            even: Colour of rows 0, 2, 4... (the stripes).
        """
        self._odd, self._even = QColor(odd), QColor(even)
        self.viewport().update()

    def row_colors(self) -> tuple[QColor, QColor]:
        """Returns: ``(odd, even)`` background colours currently used."""
        return QColor(self._odd), QColor(self._even)

    def paintEvent(self, event):
        """Qt callback: paint the background exactly once, then the rows' contents.

        Args:
            event: Paint event; only its rectangle is repainted.
        """
        area = event.rect()
        p = QPainter(self.viewport())
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(area, self._odd)                                # empty area + odd rows
        width = self.viewport().width()
        for row in range(0, self.rowCount(), 2):                   # even rows: stripes
            top, height = self.rowViewportPosition(row), self.rowHeight(row)
            if top + height >= area.top() and top <= area.bottom():
                p.fillRect(0, top, width, height, self._even)
        p.end()
        super().paintEvent(event)                                  # text, selection, focus


class NoFocusDelegate(QStyledItemDelegate):
    """Draws playlist cells normally but without the dotted keyboard-focus rectangle."""

    def paint(self, painter, option, index):
        """Qt callback to draw one cell.

        Args:
            painter: QPainter to draw with.
            option:  Style options for the cell; its "has focus" flag is cleared.
            index:   Which cell (row/column) is drawn.
        """
        option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)


class DraggableMixin:
    """Mix into a frameless QWidget subclass (listed first) so dragging anywhere on its
    background with the left mouse button moves the window. Child widgets such as
    buttons still receive their own clicks."""

    #: Mouse position relative to the window's top-left while dragging, else None.
    _drag_offset = None

    def mousePressEvent(self, event):
        """Qt callback: on left-button press, remember where in the window it was grabbed.

        Args:
            event: Qt mouse event; other buttons are passed on unchanged.
        """
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Qt callback: while the left button is held after a press, move the window
        so the grabbed point follows the mouse.

        Args:
            event: Qt mouse event with the current screen position.
        """
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Qt callback: button released, so the drag ends.

        Args:
            event: Qt mouse event, passed on to the widget.
        """
        self._drag_offset = None
        super().mouseReleaseEvent(event)
