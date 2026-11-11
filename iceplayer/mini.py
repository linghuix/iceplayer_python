"""About dialog and the 100x100 mini-mode window.

* ``AboutDialog``  the 300x400 "about" picture, opened by clicking the logo.
* ``MiniWindow``   the small round always-on-top window used in mini mode. It has no
                   playback logic of its own: every button and menu item calls the
                   main ``IcePlayer`` window, and it listens to the player's signals
                   to keep its play/pause icon and mode tick in sync.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QPainter
from PySide6.QtWidgets import QDialog, QMenu, QSlider, QWidget, QWidgetAction

from .playlist import MODE_NAMES, PlayMode
from .widgets import MENU_STYLE, SLIDER_STYLE, DraggableMixin, IceButton, pixmap


class AboutDialog(DraggableMixin, QDialog):
    """Frameless, modal 300x400 About box with a close (X) button. Draggable."""

    def __init__(self, parent=None):
        """Create the (hidden) dialog; call ``show()`` to open it.

        Args:
            parent: Window it belongs to (the main player); modal over it.
        """
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.9)
        self.setFixedSize(300, 400)
        self.setModal(True)
        self._background = pixmap("about.png")
        close = IceButton(self, "close.png", "close_hover.png", (20, 20))
        close.setGeometry(255, 12, 30, 30)
        close.clicked.connect(self.close)

    def paintEvent(self, event):
        """Qt callback: draw the about.png picture.

        Args:
            event: Qt paint event (unused).
        """
        QPainter(self).drawPixmap(0, 0, self._background)


class MiniWindow(DraggableMixin, QWidget):
    """100x100 always-on-top mini player: play/pause and next buttons plus a right-click
    menu (返回主界面, volume, 下一曲, 上一曲, play modes, 桌面歌词, 退出).
    Double-click returns to the main window."""

    def __init__(self, player):
        """Create the (hidden) mini window and connect it to the main player.

        Args:
            player: The main ``IcePlayer``. Its methods are called for every action, and
                    its ``playing_changed`` / ``mode_changed`` signals keep this window
                    in sync.
        """
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.player = player
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.9)
        self.setFixedSize(100, 100)
        self._background = pixmap("mini.png")

        self.play_button = IceButton(self, "mini_play.png", None, (34, 34))
        self.play_button.setGeometry(29, 29, 34, 34)
        self.pause_button = IceButton(self, "mini_pause.png", None, (34, 34))
        self.pause_button.setGeometry(24, 29, 34, 34)
        self.pause_button.hide()
        next_button = IceButton(self, "mini_next.png", None, (9, 15))
        next_button.setGeometry(61, 48, 34, 34)

        self.play_button.clicked.connect(player.play_clicked)
        self.pause_button.clicked.connect(player.pause_clicked)
        next_button.clicked.connect(player.next_clicked)
        player.playing_changed.connect(self.set_playing)
        player.mode_changed.connect(self._sync_mode)

        self._build_menu()

    def _build_menu(self):
        """Create the right-click menu and its volume slider and play-mode items.
        Takes no inputs; stores ``self.menu``, ``self.vol_slider`` and ``self.mode_group``."""
        self.menu = QMenu(self)
        self.menu.setStyleSheet(MENU_STYLE)
        self.menu.addAction("返回主界面", self.back_to_main)
        self.menu.addSeparator()

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setFixedSize(120, 25)
        self.vol_slider.setStyleSheet(SLIDER_STYLE)
        self.vol_slider.valueChanged.connect(self.player.set_volume)
        vol_action = QWidgetAction(self.menu)
        vol_action.setDefaultWidget(self.vol_slider)
        self.menu.addAction(vol_action)
        self.menu.addSeparator()

        self.menu.addAction("下一曲", self.player.next_clicked)
        self.menu.addAction("上一曲", self.player.previous_clicked)
        self.menu.addSeparator()

        self.mode_group = QActionGroup(self)
        for mode in PlayMode:
            action = self.mode_group.addAction(MODE_NAMES[mode])
            action.setCheckable(True)
            action.setData(int(mode))
        self.mode_group.triggered.connect(lambda a: self.player.set_play_mode(PlayMode(a.data())))
        self.menu.addActions(self.mode_group.actions())
        self.menu.addSeparator()

        self.menu.addAction("桌面歌词", self.player.toggle_lyrics)
        self.menu.addSeparator()
        self.menu.addAction("退出", self.player.close)

    def _sync_mode(self, mode: int):
        """Tick the menu item for a play mode (called when the main window changes mode).

        Args:
            mode: ``PlayMode`` value 0-3.
        """
        self.mode_group.actions()[mode].setChecked(True)

    def set_playing(self, playing: bool):
        """Show the pause icon while playing, otherwise the play icon.

        Args:
            playing: True if music is playing.
        """
        self.play_button.setVisible(not playing)
        self.pause_button.setVisible(playing)

    def show_mini(self):
        """Copy the player's current mode, play state and volume into this window, then
        show it. Takes no inputs. The volume slider is updated without emitting, so
        opening mini mode doesn't change the volume."""
        self._sync_mode(self.player.play_mode)
        self.set_playing(self.player.is_playing())
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(self.player.volume_button.volume())
        self.vol_slider.blockSignals(False)
        self.show()

    def back_to_main(self):
        """Show and focus the main window, and hide this mini window."""
        self.player.show()
        self.player.activateWindow()
        self.hide()

    def mouseDoubleClickEvent(self, event):
        """Qt callback: a double-click returns to the main window.

        Args:
            event: Qt mouse event (unused).
        """
        self.back_to_main()

    def paintEvent(self, event):
        """Qt callback: draw the round mini.png background.

        Args:
            event: Qt paint event (unused).
        """
        QPainter(self).drawPixmap(0, 0, self._background)

    def contextMenuEvent(self, event):
        """Qt callback on right-click: open the mini menu at the mouse.

        Args:
            event: Qt context-menu event; its ``globalPos()`` places the menu.
        """
        self.menu.exec(event.globalPos())
