"""LRC lyric files and the always-on-top desktop lyric window.

* ``_decode``       raw .lrc bytes -> text (guesses UTF-8 / UTF-16 / GBK).
* ``parse_lrc``     text -> sorted list of (milliseconds, lyric line).
* ``load_lrc_for``  find and read ``song.lrc`` next to ``song.mp3``.
* ``Lyrics``        answers "which line should show at this playback position?".
* ``LyricWindow``   the 800x80 floating lyric bar.
"""
import bisect
import re
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from .widgets import FONT_FAMILY, DraggableMixin, pixmap

# One time tag: [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx] or [mm:ss:xx].
_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# Common Chinese characters (CJK Unified Ideographs).
_CJK = re.compile(r"[\u4e00-\u9fff]")
# Anything outside: ASCII/Latin, general punctuation, CJK symbols + kana, CJK, hangul, full-width forms.
_UNUSUAL = re.compile(r"[^\x00-\u024f\u2000-\u206f\u3000-\u30ff\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]")


def _decode(raw: bytes) -> str:
    """Turn the bytes of a lyric file into text, guessing the encoding.

    Args:
        raw: The whole file content.

    Returns:
        The decoded text. Order of checks:
        1. UTF-16 or UTF-8 byte-order mark (BOM) -> that encoding.
        2. NUL bytes (written by the old C++ IcePlayer) are removed.
        3. Try UTF-8 and GB18030 (a superset of GBK). If both work, prefer GBK when
           the UTF-8 reading contains unusual scripts but the GBK reading is Chinese
           (short GBK text such as "一" is also valid UTF-8 Cyrillic).
        4. Otherwise UTF-8, then GBK, then UTF-8 with bad bytes replaced. Never raises.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    raw = raw.replace(b"\x00", b"")  # files written by the old C++ version via QDataStream
    try:
        utf8 = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        utf8 = None
    try:
        gbk = raw.decode("gb18030")
    except UnicodeDecodeError:
        gbk = None
    # Some short GBK byte sequences are also valid UTF-8 (e.g. "一" = D2 BB = Cyrillic "һ").
    # If the UTF-8 reading has characters from outside the scripts lyrics normally use
    # (Latin, CJK, kana, hangul) while the GBK reading is Chinese, the GBK reading wins.
    if utf8 is not None and gbk is not None and _UNUSUAL.search(utf8) and _CJK.search(gbk):
        return gbk
    if utf8 is not None:
        return utf8
    if gbk is not None:
        return gbk
    return raw.decode("utf-8", errors="replace")


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """Read LRC lyric text into a timeline.

    Args:
        text: Lyric file content, e.g. ``"[00:05.54]hello\\n[00:08.00][01:10.00]chorus"``.

    Returns:
        A list of ``(milliseconds, line)`` sorted by time, e.g.
        ``[(5540, "hello"), (8000, "chorus"), (70000, "chorus")]``.
        A line with several time tags appears once per tag. Lines without a time tag
        (including ``[ti:...]`` / ``[ar:...]`` headers) are skipped; text is trimmed.
    """
    entries = []
    for line in text.splitlines():
        tags = list(_TAG.finditer(line))
        if not tags:
            continue
        lyric = _TAG.sub("", line).strip()
        for m in tags:
            minutes, seconds, frac = int(m[1]), int(m[2]), m[3] or "0"
            ms = int(frac.ljust(3, "0")[:3])  # ".5" -> 500, ".54" -> 540, ".543" -> 543
            entries.append((minutes * 60000 + seconds * 1000 + ms, lyric))
    entries.sort(key=lambda e: e[0])
    return entries


def load_lrc_for(audio_path: str) -> list[tuple[int, str]]:
    """Load the lyrics that belong to a song.

    Args:
        audio_path: Path of the song, e.g. ``"D:/Music/song.mp3"``.

    Returns:
        The parsed timeline from ``D:/Music/song.lrc`` (see ``parse_lrc``), or an
        empty list if that file doesn't exist or can't be read.
    """
    lrc = Path(audio_path).with_suffix(".lrc")
    if not lrc.is_file():
        return []
    try:
        return parse_lrc(_decode(lrc.read_bytes()))
    except OSError:
        return []


class Lyrics:
    """A song's lyric timeline with fast "current line" lookup.

    ``bool(lyrics)`` is False when the song has no lyrics.
    """

    def __init__(self, entries=()):
        """Store the timeline.

        Args:
            entries: Time-sorted ``(milliseconds, line)`` pairs from ``parse_lrc``;
                     empty (the default) means "no lyrics".
        """
        self.entries = list(entries)
        self._times = [t for t, _ in self.entries]

    def __bool__(self):
        """Returns: True if there is at least one lyric line."""
        return bool(self.entries)

    def line_at(self, position_ms: int) -> str | None:
        """Find the line to display at a playback position.

        Args:
            position_ms: Playback position in milliseconds.

        Returns:
            The last line whose time is <= ``position_ms`` (it stays shown until the
            next line starts), or None before the first line / when there are no lyrics.
        """
        i = bisect.bisect_right(self._times, position_ms) - 1
        return self.entries[i][1] if i >= 0 else None


class LyricWindow(DraggableMixin, QWidget):
    """800x80 frameless, always-on-top desktop lyric bar (ICE_Lrc in the C++ version).

    Drag it with the left mouse button; right-click → 隐藏 hides it.
    """

    def __init__(self):
        """Create the (hidden) bar showing the app name 冰点播放器. Takes no inputs."""
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(800, 80)
        self._background = pixmap("lrc_background.png")
        self._font = QFont(FONT_FAMILY, 25)
        self._text = "冰点播放器"
        self.menu = QMenu(self)
        self.menu.addAction("隐藏", self.hide)

    def text(self) -> str:
        """Returns: the lyric text currently set (before the "Music..." substitution)."""
        return self._text

    def set_text(self, text: str):
        """Show a new lyric line.

        Args:
            text: The line to display. Repainting happens only if it changed.
        """
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, event):
        """Qt callback: draw the background and the centred blue text with a grey shadow.

        Args:
            event: Qt paint event (unused). Text shorter than 2 characters is drawn
                   as "Music..." like the original.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.drawPixmap(0, 0, self._background)
        p.setFont(self._font)
        text = self._text if len(self._text) >= 2 else "Music..."
        rect = QRect(0, 6, 800, 60)
        p.setPen(QColor(90, 105, 115, 200))  # shadow
        p.drawText(rect.translated(1, 1), Qt.AlignCenter, text)
        p.setPen(QColor(1, 150, 255))
        p.drawText(rect, Qt.AlignCenter, text)

    def contextMenuEvent(self, event):
        """Qt callback on right-click: open the menu with 隐藏 (hide).

        Args:
            event: Qt context-menu event; its ``globalPos()`` places the menu.
        """
        self.menu.exec(event.globalPos())
