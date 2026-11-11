"""Main 400x600 player window (IcePlayer in the C++ version).

The window owns everything: the Qt media player, the playlist table, the buttons,
and the three side windows (lyric bar, mini window, About box). Methods are grouped:

* setup             build the media player, widgets and menus
* playlist          add / remove / clear songs, file dialogs, right-click menu
* playback          play, pause (with fade), next/previous, modes, volume, seeking
* player callbacks  react to Qt media events (state, end of song, position, tags)
* windows etc.      lyric bar, mini mode, saving, painting, drag & drop, closing
"""
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QActionGroup, QColor, QCursor, QFont, QKeySequence, QPainter,
                           QPalette, QPixmap, QShortcut)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame,
                               QLabel, QMenu, QSlider, QTableWidget, QTableWidgetItem,
                               QWidget)

from .lyrics import Lyrics, LyricWindow, load_lrc_for
from .mini import AboutDialog, MiniWindow
from .playlist import (AUDIO_EXTENSIONS, MODE_NAMES, PlayMode, is_audio, load_state,
                       next_index, previous_index, save_state)
from .widgets import (FONT_FAMILY, MENU_STYLE, SLIDER_STYLE, DraggableMixin, IceButton,
                      NoFocusDelegate, ScrollingLabel, VolumeButton, pixmap)

SCROLL_THRESHOLD = 28   # titles longer than this marquee-scroll
TEXT_SPEED = 500        # ms per scroll step
ROW_EVEN = QColor(213, 228, 242)   # light blue stripe for rows 0, 2, 4...
ROW_ODD = QColor(255, 255, 255)    # white for rows 1, 3, 5...
INVALID = "失效"                    # status shown for missing or unplayable files


def fmt_ms(ms: int) -> str:
    """Format a time for the time label.

    Args:
        ms: Time in milliseconds (negative values count as 0).

    Returns:
        ``"mm:ss"``, e.g. 65000 -> ``"01:05"``. Partial seconds are dropped.
    """
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


class IcePlayer(DraggableMixin, QWidget):
    """The main window.

    Signals:
        playing_changed(bool): True when playback starts, False when it pauses/stops.
        mode_changed(int):     new ``PlayMode`` value after the play mode changes.

    Main attributes:
        files (list[str]):   song paths in playlist order (row i = files[i]).
        current_index (int): playlist row of the loaded song, -1 if none.
        play_mode (PlayMode): active play mode.
        lyrics (Lyrics):     lyrics of the current song (empty if none).
        media / audio:       the QMediaPlayer and its QAudioOutput.
    """

    playing_changed = Signal(bool)
    mode_changed = Signal(int)

    def __init__(self, state_file: Path):
        """Build the window and restore the saved playlist, volume and mode.

        Args:
            state_file: JSON file the playlist/volume/mode are loaded from and saved to
                        (normally IceList.json). Nothing is written while starting up.
        """
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.state_file = state_file
        self.files: list[str] = []
        self.current_index = -1
        self.play_mode = PlayMode.SINGLE
        self.lyrics = Lyrics()
        self._background = pixmap("background.png")
        self._default_cover = pixmap("default_album.jpg")

        self._init_player()
        self._init_ui()
        self._init_menus()
        self.lyric_window = LyricWindow()
        self.mini_window = MiniWindow(self)
        self.about_dialog = AboutDialog(self)

        self._fade_timer = QTimer(self, interval=10)
        self._fade_timer.timeout.connect(self._fade_step)

        state = load_state(state_file)
        self.volume_button.set_volume(state["volume"])
        self.set_play_mode(PlayMode(state["mode"]))
        self.add_files(state["files"], save=False)
        self._reset_info()
        self._ready = True   # from now on, changes are saved (see save_state)

    # ------------------------------------------------------------------ setup
    def _init_player(self):
        """Create the QMediaPlayer + QAudioOutput and connect their signals to the
        ``_on_*`` callbacks. A playback error marks the current row 失效."""
        self.audio = QAudioOutput(self)
        self.media = QMediaPlayer(self)
        self.media.setAudioOutput(self.audio)
        self.media.positionChanged.connect(self._on_position)
        self.media.durationChanged.connect(self._on_duration)
        self.media.playbackStateChanged.connect(self._on_state)
        self.media.mediaStatusChanged.connect(self._on_media_status)
        self.media.metaDataChanged.connect(self._on_metadata)
        self.media.errorOccurred.connect(lambda *_: self._mark_row(self.current_index, INVALID))

    def _init_ui(self):
        """Create every widget at the same position/size as the C++ version, connect the
        buttons, set up the playlist table, and bind the Space key to play/pause."""
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.9)
        self.setFixedSize(400, 600)
        self.setWindowTitle("冰点播放器")
        self.setAcceptDrops(True)

        self.cover = QLabel(self)
        self.cover.setGeometry(40, 45, 100, 100)
        self.cover.setScaledContents(True)

        blue = QPalette()
        blue.setColor(QPalette.WindowText, QColor(1, 149, 255))
        small = QFont(FONT_FAMILY, 9)
        self.name_label = ScrollingLabel(self)
        self.name_label.setGeometry(150, 55, 220, 22)
        self.name_label.setFont(QFont(FONT_FAMILY, 12))
        self.artist_label = QLabel(self)
        self.artist_label.setGeometry(150, 85, 220, 16)
        self.album_label = QLabel(self)
        self.album_label.setGeometry(150, 115, 220, 16)
        for label in (self.name_label, self.artist_label, self.album_label):
            label.setPalette(blue)
        self.artist_label.setFont(small)
        self.album_label.setFont(small)

        self.progress = QSlider(Qt.Horizontal, self)
        self.progress.setGeometry(40, 150, 290, 20)
        self.progress.setStyleSheet(SLIDER_STYLE)
        self.progress.setEnabled(False)
        # Only seek on release, so dragging doesn't produce a stutter of sound.
        self.progress.sliderReleased.connect(self._seek_to_slider)
        self.progress.actionTriggered.connect(self._on_slider_click)

        self.time_label = QLabel("00:00", self)
        self.time_label.setGeometry(340, 150, 51, 16)
        self.time_label.setFont(small)
        time_pal = QPalette()
        time_pal.setColor(QPalette.WindowText, QColor(106, 182, 240))
        self.time_label.setPalette(time_pal)

        def button(icon, geom, size):
            """Make an IceButton.

            Args:
                icon: image base name; ``icon.png`` normal, ``icon_hover.png`` on hover.
                geom: ``(x, y, width, height)`` inside the window.
                size: image size ``(width, height)``.

            Returns:
                The new IceButton.
            """
            b = IceButton(self, f"{icon}.png", f"{icon}_hover.png", size)
            b.setGeometry(*geom)
            return b

        self.add_button = button("add", (40, 190, 31, 31), (25, 25))
        self.lyric_button = button("lyric_btn", (80, 190, 31, 31), (25, 25))
        self.prev_button = button("prev", (120, 185, 41, 41), (40, 40))
        self.play_button = button("play", (170, 175, 61, 61), (60, 60))
        self.pause_button = button("pause", (170, 175, 61, 61), (60, 60))
        self.pause_button.hide()
        self.next_button = button("next", (240, 185, 41, 41), (40, 40))
        self.mode_button = button("mode", (290, 190, 31, 31), (25, 25))
        self.minimize_button = button("minimize", (307, 3, 29, 31), (20, 20))
        self.mini_mode_button = button("mini_mode", (337, 3, 29, 31), (20, 20))
        self.close_button = button("close", (364, 3, 29, 31), (20, 20))
        self.logo_button = button("logo", (8, 3, 120, 48), (120, 48))

        self.volume_button = VolumeButton(self)
        self.volume_button.setGeometry(333, 193, 31, 31)
        self.volume_button.volume_changed.connect(lambda v: self.audio.setVolume(v / 100))

        self.add_button.clicked.connect(self.open_files)
        self.lyric_button.clicked.connect(self.toggle_lyrics)
        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_clicked)
        self.pause_button.clicked.connect(self.pause_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.mode_button.clicked.connect(lambda: self.mode_menu.exec(QCursor.pos()))
        self.minimize_button.clicked.connect(self.showMinimized)
        self.mini_mode_button.clicked.connect(self.enter_mini_mode)
        self.close_button.clicked.connect(self.close)
        self.logo_button.clicked.connect(lambda: self.about_dialog.show())

        self.table = QTableWidget(0, 2, self)
        self.table.setGeometry(25, 240, 350, 320)
        self.table.setItemDelegate(NoFocusDelegate(self.table))
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 45)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(lambda row, _col: self.play_index(row))
        self.table.setStyleSheet(
            "QTableWidget{background:white;selection-background-color:#9fd2ff;selection-color:black;}"
            "QScrollBar{background:transparent;width:5px;}"
            "QScrollBar::handle{background:lightgray;border-radius:2px;}"
            "QScrollBar::handle:hover{background:gray;}"
            "QScrollBar::sub-line,QScrollBar::add-line{background:transparent;height:0px;}")

        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)

    def _init_menus(self):
        """Create the play-mode menu (4 checkable items, one ticked at a time) and the
        playlist's right-click menu (filled each time it opens, see ``_table_menu``)."""
        self.mode_menu = QMenu(self)
        self.mode_menu.setStyleSheet(MENU_STYLE)
        self.mode_group = QActionGroup(self)
        for mode in PlayMode:
            action = self.mode_group.addAction(MODE_NAMES[mode])
            action.setCheckable(True)
            action.setData(int(mode))
        self.mode_group.triggered.connect(lambda a: self.set_play_mode(PlayMode(a.data())))
        self.mode_menu.addActions(self.mode_group.actions())

        self.table_menu = QMenu(self)
        self.table_menu.setStyleSheet(MENU_STYLE)

    # -------------------------------------------------------------- playlist
    def add_files(self, paths, save=True):
        """Append songs to the end of the playlist.

        Args:
            paths: Iterable of file paths (str or Path). Paths already in the list are
                   skipped; files that don't exist are added but shown as 失效.
            save:  Write the playlist file afterwards (False while loading at start-up).
        """
        for path in paths:
            path = str(Path(path))
            if path in self.files:
                continue
            self.files.append(path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate((Path(path).stem, "" if Path(path).exists() else INVALID)):
                item = QTableWidgetItem(text)
                item.setFont(QFont(FONT_FAMILY, 10))
                if col == 1:
                    item.setForeground(QColor(200, 80, 80))
                self.table.setItem(row, col, item)
        self._color_table()
        if save:
            self.save_state()

    def remove_row(self, row: int):
        """Remove one song from the playlist (移除本曲) and save.

        Args:
            row: Playlist row; out-of-range values are ignored. Removing the playing
                 song stops playback; removing a row above it keeps ``current_index``
                 pointing at the same song.
        """
        if not 0 <= row < len(self.files):
            return
        if row == self.current_index:
            self.unload()
            self.current_index = -1
            self._reset_info()
        elif row < self.current_index:
            self.current_index -= 1
        del self.files[row]
        self.table.removeRow(row)
        self._color_table()
        self.save_state()

    def clear_list(self):
        """Stop playback, remove every song (移除所有), reset the display and save."""
        self.unload()
        self.files.clear()
        self.table.setRowCount(0)
        self.current_index = -1
        self._reset_info()
        self.save_state()

    def open_files(self):
        """Ask the user for audio files (添加歌曲 dialog) and add them. Cancel adds nothing."""
        patterns = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "添加音乐", "", f"音频文件 ({patterns});;MP3 (*.mp3)")
        self.add_files(files)

    def open_dir(self):
        """Ask the user for a folder (添加目录) and add its audio files, sorted by name.
        Sub-folders are not searched; Cancel adds nothing."""
        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if directory:
            self.add_files(sorted(str(p) for p in Path(directory).iterdir() if p.is_file() and is_audio(str(p))))

    def open_and_play(self, path: str):
        """Add a song (if new) and play it at once. Used for command-line / "Open with" /
        second-instance file arguments.

        Args:
            path: Song path; empty strings and non-existent files are ignored.
        """
        if not path or not Path(path).is_file():
            return
        path = str(Path(path))
        self.add_files([path])
        self.play_index(self.files.index(path))

    def _table_menu(self, pos):
        """Show the playlist's right-click menu.

        Args:
            pos: Click position inside the table viewport. On a song the menu also
                 offers 移除本曲 for that row; on empty space it doesn't.
        """
        self.table_menu.clear()
        self.table_menu.addAction("添加歌曲", self.open_files)
        self.table_menu.addAction("添加目录", self.open_dir)
        self.table_menu.addSeparator()
        item = self.table.itemAt(pos)
        if item is not None:
            row = item.row()
            self.table_menu.addAction("移除本曲", lambda: self.remove_row(row))
        self.table_menu.addAction("移除所有", self.clear_list)
        self.table_menu.exec(self.table.viewport().mapToGlobal(pos))

    def _color_table(self):
        """Repaint the row stripes (blue/white) and make the playing row bold."""
        for row in range(self.table.rowCount()):
            playing = row == self.current_index
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(ROW_EVEN if row % 2 == 0 else ROW_ODD)
                    font = item.font()
                    font.setBold(playing)
                    item.setFont(font)

    def _mark_row(self, row: int, text: str):
        """Set the status column of a row.

        Args:
            row:  Playlist row (ignored if out of range, e.g. -1).
            text: ``INVALID`` ("失效") or "" to clear it.
        """
        if 0 <= row < self.table.rowCount():
            self.table.item(row, 1).setText(text)

    # -------------------------------------------------------------- playback
    def play_index(self, row: int) -> bool:
        """Load and play the song in a playlist row.

        Args:
            row: Playlist row.

        Returns:
            True if playback was started. False if the row is out of range or the file
            is missing (the row is then marked 失效 and nothing changes).
        """
        if not 0 <= row < len(self.files):
            return False
        path = self.files[row]
        if not Path(path).is_file():
            self._mark_row(row, INVALID)
            return False
        self._mark_row(row, "")
        self._stop_fade()
        self.current_index = row
        self.media.setSource(QUrl.fromLocalFile(path))
        self.media.play()
        self._load_song_extras(path)
        self._color_table()
        self.table.scrollToItem(self.table.item(row, 0))
        return True

    def _play_from(self, index_fn):
        """Play the first playable song chosen by ``index_fn``, skipping missing files.

        Args:
            index_fn: Function ``(current_row) -> next_row or None`` (e.g. a wrapper of
                      ``next_index``). It is called repeatedly from the last candidate
                      until a song plays, it returns None, or a row repeats (so a list
                      of only missing files can't loop forever).
        """
        tried = set()
        idx = index_fn(self.current_index)
        while idx is not None and idx not in tried:
            tried.add(idx)
            if self.play_index(idx):
                return
            idx = index_fn(idx)

    def next_clicked(self):
        """Next button: play the next song (random one in 随机播放), wrapping around."""
        self._play_from(lambda cur: next_index(cur, len(self.files), self.play_mode, auto=False))

    def previous_clicked(self):
        """Previous button: play the previous song (random one in 随机播放), wrapping around.
        With nothing played yet it starts the last song."""
        self._play_from(lambda cur: previous_index(max(cur, 0), len(self.files), self.play_mode))

    def play_clicked(self):
        """Play button: resume if paused; replay the loaded song if stopped; otherwise
        start the first playable song. Does nothing with an empty playlist."""
        state = self.media.playbackState()
        if state == QMediaPlayer.PausedState:
            self._stop_fade()
            self.media.play()
        elif state == QMediaPlayer.StoppedState:
            if self.current_index >= 0 and self.media.source().isValid():
                self.media.play()
            elif self.files:
                self._play_from(lambda cur: next_index(cur, len(self.files), PlayMode.LIST_LOOP, False))

    def pause_clicked(self):
        """Pause button: fade the volume out over ~0.3 s, then pause (the C++ version did
        this with Sleep()). Ignored if not playing or already fading."""
        if self.media.playbackState() == QMediaPlayer.PlayingState and not self._fade_timer.isActive():
            self._fade_timer.start()

    def toggle_play(self):
        """Space key: pause if playing, otherwise play (see the two methods above)."""
        if self.is_playing():
            self.pause_clicked()
        else:
            self.play_clicked()

    def _fade_step(self):
        """Fade timer callback (every 10 ms): lower the volume a little; at zero, pause
        and restore the user's volume."""
        v = self.audio.volume() - 0.035
        if v <= 0:
            self.media.pause()
            self._stop_fade()
        else:
            self.audio.setVolume(v)

    def _stop_fade(self):
        """Cancel a running fade and set the audio back to the volume button's value."""
        self._fade_timer.stop()
        self.audio.setVolume(self.volume_button.volume() / 100)

    def unload(self):
        """Stop playback and release the song.

        Unloads the source *before* stopping: with Qt 6.8's FFmpeg backend, calling
        stop() after seeking while paused blocks forever.
        """
        self._stop_fade()
        self.media.setSource(QUrl())
        self.media.stop()

    def is_playing(self) -> bool:
        """Returns: True if audio is playing right now (not paused or stopped)."""
        return self.media.playbackState() == QMediaPlayer.PlayingState

    def set_play_mode(self, mode: PlayMode):
        """Change the play mode, tick it in the menu, notify the mini window and save.

        Args:
            mode: A ``PlayMode`` or its int value 0-3.
        """
        self.play_mode = PlayMode(mode)
        self.mode_group.actions()[self.play_mode].setChecked(True)
        self.mode_changed.emit(int(self.play_mode))
        self.save_state()

    def set_volume(self, value: int):
        """Set the volume (also moves the volume slider and changes the audio output).

        Args:
            value: Volume 0-100.
        """
        self.volume_button.set_volume(value)

    def _seek_to_slider(self):
        """Progress bar released: jump to the slider position, and resume if not playing."""
        self.media.setPosition(self.progress.value())
        if not self.is_playing():
            self.play_clicked()

    def _on_slider_click(self, action):
        """Progress bar action: a click on the empty groove jumps playback at once.

        Args:
            action: QSlider action as a plain int (``SliderPageStepAdd/Sub`` for groove
                    clicks; handle drags are ignored here and seek on release).
        """
        # Clicking on the groove (page step) seeks immediately.
        # actionTriggered passes a plain int, which never equals the enum member itself.
        if action in (QSlider.SliderAction.SliderPageStepAdd.value,
                      QSlider.SliderAction.SliderPageStepSub.value):
            QTimer.singleShot(0, lambda: self.media.setPosition(self.progress.sliderPosition()))

    # ------------------------------------------------------ player callbacks
    def _on_state(self, state):
        """Media callback: playback state changed.

        Args:
            state: ``QMediaPlayer.PlaybackState``. Shows the pause button while playing,
                   the play button otherwise, and emits ``playing_changed``.
        """
        playing = state == QMediaPlayer.PlayingState
        self.play_button.setVisible(not playing)
        self.pause_button.setVisible(playing)
        self.playing_changed.emit(playing)

    def _on_media_status(self, status):
        """Media callback: loading/ending status changed.

        Args:
            status: ``QMediaPlayer.MediaStatus``. At ``EndOfMedia`` the next song is
                    chosen by the play mode (单曲循环 restarts the same song). At
                    ``InvalidMedia`` (not really audio) the row is marked 失效.
        """
        if status == QMediaPlayer.EndOfMedia:
            if self.play_mode == PlayMode.SINGLE_LOOP:
                self.media.setPosition(0)
                self.media.play()
            else:
                self._play_from(lambda cur: next_index(cur, len(self.files), self.play_mode, auto=True))
        elif status == QMediaPlayer.InvalidMedia:
            self._mark_row(self.current_index, INVALID)

    def _on_duration(self, duration):
        """Media callback: song length known.

        Args:
            duration: Length in milliseconds. Sets the progress bar range (disabled for
                      0) and makes a groove click jump 1/10 of the song.
        """
        self.progress.setRange(0, duration)
        self.progress.setEnabled(duration > 0)
        self.progress.setPageStep(max(1, duration // 10))

    def _on_position(self, position):
        """Media callback: playback position moved (several times per second).

        Args:
            position: Position in milliseconds. Updates the progress bar (unless the user
                      is dragging it), the time label, and the lyric bar line.
        """
        if not self.progress.isSliderDown():
            self.progress.setValue(position)
        self.time_label.setText(fmt_ms(position))
        if self.lyrics:
            line = self.lyrics.line_at(position)
            if line is not None:
                self.lyric_window.set_text(line)

    def _on_metadata(self):
        """Media callback: the song's tags were read. Shows title (or file name), artist
        (or 未知音乐家), album (or 未知专辑); uses embedded cover art if there is no
        song.jpg. Ignored when no song is selected."""
        if self.current_index < 0:
            return
        md = self.media.metaData()
        path = Path(self.files[self.current_index])
        title = md.stringValue(QMediaMetaData.Key.Title) or path.stem
        artist = (md.stringValue(QMediaMetaData.Key.ContributingArtist)
                  or md.stringValue(QMediaMetaData.Key.AlbumArtist) or "未知音乐家")
        album = md.stringValue(QMediaMetaData.Key.AlbumTitle) or "未知专辑"
        self._set_title(title)
        self.artist_label.setText(artist)
        self.album_label.setText(album)
        if not self.lyrics:
            self.lyric_window.set_text(title)
        if not path.with_suffix(".jpg").is_file():
            for key in (QMediaMetaData.Key.CoverArtImage, QMediaMetaData.Key.ThumbnailImage):
                image = md.value(key)
                if image is not None and hasattr(image, "isNull") and not image.isNull():
                    self.cover.setPixmap(QPixmap.fromImage(image))
                    break

    def _load_song_extras(self, path: str):
        """Load the lyrics and cover stored next to a song and show its file name until
        the tags arrive.

        Args:
            path: Song path; reads ``song.lrc`` and ``song.jpg`` from the same folder
                  (default cover if there is no .jpg).
        """
        self.lyrics = Lyrics(load_lrc_for(path))
        self._set_title(Path(path).stem)
        self.artist_label.setText("")
        self.album_label.setText("")
        self.lyric_window.set_text(Path(path).stem)
        cover = QPixmap(str(Path(path).with_suffix(".jpg")))
        self.cover.setPixmap(cover if not cover.isNull() else self._default_cover)

    def _set_title(self, title: str):
        """Show the song title; titles longer than 28 characters scroll.

        Args:
            title: Text for the name label.
        """
        if len(title) > SCROLL_THRESHOLD:
            self.name_label.set_scrolling_text(title, TEXT_SPEED)
        else:
            self.name_label.stop()
            self.name_label.setText(title)

    def _reset_info(self):
        """Back to the "nothing loaded" look: placeholder labels, 00:00, default cover,
        disabled progress bar, no lyrics, app name in the lyric bar."""
        self.name_label.stop()
        self.name_label.setText("Name")
        self.artist_label.setText("Musician")
        self.album_label.setText("Album")
        self.time_label.setText("00:00")
        self.cover.setPixmap(self._default_cover)
        self.progress.setValue(0)
        self.progress.setEnabled(False)
        self.lyrics = Lyrics()
        self.lyric_window.set_text("冰点播放器")

    # ---------------------------------------------------------- windows etc.
    def toggle_lyrics(self):
        """Lyric button: show or hide the desktop lyric bar. The first time it is shown
        it is placed centred near the bottom of the screen."""
        self.lyric_window.setVisible(self.lyric_window.isHidden())
        if self.lyric_window.isVisible() and not getattr(self, "_lyric_placed", False):
            screen = self.screen().availableGeometry()
            self.lyric_window.move(screen.center().x() - 400, screen.bottom() - 140)
            self._lyric_placed = True

    def enter_mini_mode(self):
        """Mini-mode button: hide this window and show the mini window (top-right of the
        screen the first time), synced with the current state."""
        if not getattr(self, "_mini_placed", False):
            screen = self.screen().availableGeometry()
            self.mini_window.move(screen.right() - 160, screen.top() + 60)
            self._mini_placed = True
        self.mini_window.show_mini()
        self.hide()

    def bring_to_front(self):
        """Leave mini mode / un-minimise and bring this window to the front with focus
        (used when the app is launched a second time)."""
        self.mini_window.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def save_state(self):
        """Write playlist, volume and play mode to ``state_file``. Does nothing until
        start-up has finished, so loading never overwrites the saved file."""
        if getattr(self, "_ready", False):
            save_state(self.state_file, self.files, self.volume_button.volume(), self.play_mode)

    def paintEvent(self, event):
        """Qt callback: draw the window's background image.

        Args:
            event: Qt paint event (unused).
        """
        QPainter(self).drawPixmap(0, 0, self._background)

    def dragEnterEvent(self, event):
        """Qt callback: accept a drag only if it carries files/URLs.

        Args:
            event: Qt drag-enter event.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Qt callback: add dropped audio files, plus the audio files of dropped folders
        (sorted, not recursive). Other files are ignored.

        Args:
            event: Qt drop event with the dropped file URLs.
        """
        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                paths += sorted(str(f) for f in p.iterdir() if f.is_file() and is_audio(str(f)))
            elif p.is_file() and is_audio(str(p)):
                paths.append(str(p))
        self.add_files(paths)

    def closeEvent(self, event):
        """Qt callback when the window closes: save, stop playback, close the lyric and
        mini windows, and quit the application.

        Args:
            event: Qt close event (accepted).
        """
        self.save_state()
        self.unload()
        self.lyric_window.close()
        self.mini_window.close()
        event.accept()
        QApplication.quit()
