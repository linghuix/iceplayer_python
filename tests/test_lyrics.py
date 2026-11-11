"""Tests for iceplayer/lyrics.py — lyric files and the desktop lyric bar.

What is tested
--------------
1. ``_decode``      Turning the raw bytes of a .lrc file into text. Lyric files come in
                    UTF-8, UTF-8 with BOM, UTF-16 and Chinese GBK; files written by the
                    old C++ IcePlayer also contain stray NUL bytes.
2. ``parse_lrc``    Reading time tags such as ``[01:02.50]`` into (milliseconds, text)
                    pairs, sorted by time.
3. ``load_lrc_for`` Finding ``song.lrc`` next to ``song.mp3``.
4. ``Lyrics``       Picking the line that should show at a given playback position.
5. ``LyricWindow``  The 800x80 always-on-top lyric bar: size, flags, text, painting,
                    and its right-click "隐藏" (hide) menu.

How it is tested
----------------
Mostly plain input → expected output checks. ``@pytest.mark.parametrize`` runs one
test function once per row of example data, so each row shows up as its own test
(e.g. ``test_parse_time_formats[[01:02.5]-62500]``). The window tests build a real
LyricWindow off-screen and render it to an image with ``grab()``.

How to run
----------
    python -m pytest tests/test_lyrics.py -v
    python -m pytest tests/test_lyrics.py -k parse      # only tests with "parse" in the name
"""
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent

from iceplayer.lyrics import Lyrics, LyricWindow, _decode, load_lrc_for, parse_lrc


# =============================================================== _decode (bytes -> text)
@pytest.mark.parametrize("raw, expected", [
    ("歌词".encode("utf-8"), "歌词"),                 # plain UTF-8
    ("歌词".encode("utf-8-sig"), "歌词"),             # UTF-8 with BOM (Windows Notepad)
    ("歌词".encode("utf-16"), "歌词"),                # UTF-16 with BOM
    ("歌词".encode("gbk"), "歌词"),                   # Chinese GBK (most old .lrc files)
    ("一".encode("gbk"), "一"),                       # D2 BB is also valid UTF-8 ("һ"): must pick GBK
    ("[00:01.00]一行".encode("gbk"), "[00:01.00]一行"),
    ("English only".encode("utf-8"), "English only"),
    ("Привет".encode("utf-8-sig"), "Привет"),        # a UTF-8 BOM always wins
    ("Café déjà vu".encode("utf-8"), "Café déjà vu"),  # Latin accents stay UTF-8
    ("日本語の歌詞".encode("utf-8"), "日本語の歌詞"),     # Japanese
    ("사랑해요".encode("utf-8"), "사랑해요"),             # Korean
    ("Ñandú über “quotes”".encode("utf-8"), "Ñandú über “quotes”"),
    (b"[00:01.00]a\x00b", "[00:01.00]ab"),            # NULs from the old C++ writer are dropped
])
def test_decode_encodings(raw, expected):
    """Each supported encoding decodes to the original text.

    How: encode a known string one way, decode with ``_decode``, compare.
    """
    assert _decode(raw) == expected


def test_decode_known_tradeoff_cyrillic_without_bom():
    """Documents a deliberate limitation (this test passes when the limitation holds).

    Short Cyrillic UTF-8 without a BOM is byte-for-byte valid GBK Chinese, so it can't
    be told apart. The player prefers Chinese GBK, the common case for its users.
    If you change the detection rules, update this test on purpose.
    """
    assert _decode("Привет".encode("utf-8")) != "Привет"


def test_decode_never_raises_on_garbage():
    """Random high bytes must not crash the player; some text always comes back."""
    assert isinstance(_decode(bytes(range(128, 256))), str)


# =============================================================== parse_lrc (text -> timeline)
def test_parse_basic_line():
    """``[mm:ss.xx]text`` becomes (milliseconds, text): 5.54 s -> 5540 ms."""
    assert parse_lrc("[00:05.54]hello") == [(5540, "hello")]


@pytest.mark.parametrize("tag, ms", [
    ("[01:02]", 62000),         # no fraction
    ("[01:02.5]", 62500),       # tenths
    ("[01:02.50]", 62500),      # hundredths (the usual LRC format)
    ("[01:02.505]", 62505),     # milliseconds
    ("[01:02:50]", 62500),      # colon instead of dot before the fraction
    ("[100:00.00]", 6000000),   # 3-digit minutes (very long tracks)
])
def test_parse_time_formats(tag, ms):
    """All time-tag variants seen in real .lrc files are converted correctly."""
    assert parse_lrc(f"{tag}x") == [(ms, "x")]


def test_parse_multiple_tags_on_one_line_and_sorting():
    """A line with two tags (a repeated chorus) appears twice; output is time-sorted."""
    text = "[00:10.00]late\n[00:03.00][00:20.00]chorus\n"
    assert parse_lrc(text) == [(3000, "chorus"), (10000, "late"), (20000, "chorus")]


def test_parse_ignores_metadata_and_untagged_lines():
    """Header tags like ``[ti:Song]`` and lines without a time tag are skipped."""
    text = "[ti:Song]\n[ar:Artist]\nplain text\n\n[00:01.00]real"
    assert parse_lrc(text) == [(1000, "real")]


def test_parse_strips_whitespace_and_keeps_empty_lines():
    """Text is trimmed; a tag with no text (an instrumental gap) is kept as ""."""
    assert parse_lrc("[00:01.00]  spaced  \n[00:02.00]") == [(1000, "spaced"), (2000, "")]


def test_parse_empty():
    """An empty file gives no lyrics instead of an error."""
    assert parse_lrc("") == []


def test_parse_windows_line_endings():
    """Files saved on Windows (\\r\\n line endings) parse the same as \\n files."""
    assert parse_lrc("[00:01.00]a\r\n[00:02.00]b\r\n") == [(1000, "a"), (2000, "b")]


# =============================================================== load_lrc_for (find the file)
def test_load_lrc_for_finds_sibling_file(tmp_path):
    """For ``song.mp3`` it reads ``song.lrc`` from the same folder (GBK here)."""
    (tmp_path / "song.lrc").write_text("[00:01.00]一", encoding="gbk")
    assert load_lrc_for(str(tmp_path / "song.mp3")) == [(1000, "一")]


def test_load_lrc_for_missing_file(tmp_path):
    """No .lrc next to the song: no lyrics, no error."""
    assert load_lrc_for(str(tmp_path / "nothing.mp3")) == []


def test_load_lrc_for_directory_named_lrc(tmp_path):
    """A *folder* called song.lrc is not a lyric file and must not crash reading."""
    (tmp_path / "song.lrc").mkdir()
    assert load_lrc_for(str(tmp_path / "song.mp3")) == []


# =============================================================== Lyrics (which line is current)
def test_lyrics_truthiness():
    """``if player.lyrics:`` is False for no lyrics and True when there are lines."""
    assert not Lyrics()
    assert Lyrics([(0, "a")])


@pytest.mark.parametrize("pos, expected", [
    (-1, None),      # before the song starts
    (999, None),     # before the first lyric line (1.000 s)
    (1000, "a"),     # exactly at a line's start time
    (1500, "a"),     # between two lines: keep showing the earlier one
    (2000, "b"),
    (99999, "b"),    # after the last line: keep showing it
])
def test_lyrics_line_at(pos, expected):
    """The line shown is the last one whose time is <= the playback position."""
    assert Lyrics([(1000, "a"), (2000, "b")]).line_at(pos) == expected


def test_lyrics_empty_line_at():
    """With no lyrics there is never a current line."""
    assert Lyrics().line_at(500) is None


# =============================================================== LyricWindow (desktop lyric bar)
def test_lyric_window_defaults(qapp):
    """New bar: 800x80, frameless, always on top, showing the app name 冰点播放器."""
    w = LyricWindow()
    assert w.size().width() == 800 and w.size().height() == 80
    assert w.windowFlags() & Qt.WindowStaysOnTopHint
    assert w.windowFlags() & Qt.FramelessWindowHint
    assert w.text() == "冰点播放器"


def test_lyric_window_set_text(qapp):
    """``set_text`` changes the displayed lyric."""
    w = LyricWindow()
    w.set_text("新歌词")
    assert w.text() == "新歌词"


@pytest.mark.parametrize("text", ["", "x", "正常的一行歌词", "a" * 300])
def test_lyric_window_paints_any_text(qapp, text):
    """Painting works for empty, 1-char (shown as "Music..."), normal and very long text.

    How: render the window into an image with ``grab()``; a crash or empty image fails.
    """
    w = LyricWindow()
    w.set_text(text)
    assert not w.grab().isNull()


def test_lyric_window_context_menu_hides(qapp):
    """Right-click → "隐藏" (hide) hides the lyric bar.

    How: the real menu would wait for a click, so ``menu.exec`` is replaced by a
    function that triggers the "隐藏" action, as if the user had clicked it.
    """
    w = LyricWindow()
    w.show()

    def fake_exec(*args):
        [a for a in w.menu.actions() if a.text() == "隐藏"][0].trigger()

    with patch.object(w.menu, "exec", fake_exec):
        w.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), QPoint(5, 5)))
    assert w.isHidden()
