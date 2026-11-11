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
6. Online lyrics    ``guess_title_artist`` (what to search for), ``pick_lyrics`` (best
                    of lrclib's results), ``search_lrclib`` (the request, against a
                    fake local server, never the real internet) and ``save_lrc_for``.

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


# =============================================================== 6. online lyrics (lrclib.net)
from iceplayer.lyrics import (guess_title_artist, pick_lyrics, save_lrc_for,  # noqa: E402
                              search_lrclib)

SUNNY = "[00:28.90] 故事的小黃花\n[00:32.73] 從出生那年就飄著\n"
LIVE = "[00:01.00] live version\n"


@pytest.mark.parametrize("title, artist, stem, expected", [
    ("晴天", "周杰伦", "track01", ("晴天", "周杰伦")),          # tags win
    ("", "未知音乐家", "周杰伦 - 晴天", ("晴天", "周杰伦")),     # "Artist - Title" file name
    ("", "", "晴天", ("晴天", "")),                             # just a title
    ("晴天", "未知音乐家", "x - y", ("晴天", "")),              # placeholder artist is not searched
    ("", "Tag Artist", "Other - Song", ("Song", "Tag Artist")),  # artist tag beats file name
    ("  Song  ", " Band ", "s", ("Song", "Band")),              # spaces trimmed
])
def test_guess_title_artist(title, artist, stem, expected):
    """Input: title tag, artist tag, file name. Expected: (title, artist) to search for."""
    assert guess_title_artist(title, artist, stem) == expected


def test_pick_lyrics_prefers_matching_duration():
    """Input: a live version (299 s) listed first and the album version (270 s).
    Expected: for a 270 s song the album version wins; for 300 s the live one."""
    results = [{"duration": 299.0, "syncedLyrics": LIVE},
               {"duration": 270.0, "syncedLyrics": SUNNY}]
    assert pick_lyrics(results, 270_000) == SUNNY
    assert pick_lyrics(results, 300_000) == LIVE


def test_pick_lyrics_unknown_duration_keeps_lrclib_order():
    """Input: song length unknown (0). Expected: the first result with timed lyrics."""
    results = [{"duration": 299.0, "syncedLyrics": LIVE},
               {"duration": 270.0, "syncedLyrics": SUNNY}]
    assert pick_lyrics(results, 0) == LIVE


def test_pick_lyrics_needs_timed_lyrics():
    """Input: results with no syncedLyrics, only plainLyrics, untimed text in
    syncedLyrics, or junk. Expected: None (the lyric bar needs times); a timed result
    after them is still found."""
    useless = [{"syncedLyrics": None, "plainLyrics": "words"},
               {"plainLyrics": "words"}, {"syncedLyrics": "no time tags"}, "junk", None]
    assert pick_lyrics(useless, 270_000) is None
    assert pick_lyrics(useless + [{"syncedLyrics": SUNNY}], 270_000) == SUNNY
    assert pick_lyrics([], 0) is None


def test_pick_lyrics_bad_duration_sorts_last():
    """Input: one result with a non-numeric duration. Expected: no crash; the result
    with a real, close duration wins."""
    results = [{"duration": "?", "syncedLyrics": LIVE}, {"duration": 270, "syncedLyrics": SUNNY}]
    assert pick_lyrics(results, 270_000) == SUNNY


def test_search_lrclib_sends_title_and_artist(lrclib):
    """Input: 晴天 / 周杰伦, 270 s. Expected: one GET with track_name and artist_name
    (Chinese text URL-encoded and decoded back intact), an IcePlayer User-Agent, and
    the matching synced lyrics returned."""
    lrclib.results = [{"duration": 270.0, "syncedLyrics": SUNNY}]
    assert search_lrclib("晴天", "周杰伦", 270_000) == SUNNY
    assert len(lrclib.requests) == 1
    req = lrclib.requests[0]
    assert req["track_name"] == "晴天" and req["artist_name"] == "周杰伦"
    assert req["_ua"].startswith("IcePlayer")


def test_search_lrclib_without_artist(lrclib):
    """Input: no artist. Expected: only track_name is sent."""
    search_lrclib("Song")
    assert "artist_name" not in lrclib.requests[0] and lrclib.requests[0]["track_name"] == "Song"


def test_search_lrclib_nothing_found(lrclib):
    """Input: server answers [] or a non-list. Expected: None, no error."""
    assert search_lrclib("nothing") is None
    lrclib.results = {"error": "odd"}
    assert search_lrclib("nothing") is None


def test_search_lrclib_empty_title_sends_nothing(lrclib):
    """Input: blank title. Expected: None without asking the server."""
    assert search_lrclib("  ", "artist") is None
    assert lrclib.requests == []


def test_search_lrclib_errors_raise_oserror(lrclib, monkeypatch):
    """Input: an HTTP 500 answer, then a closed port (no server / no internet).
    Expected: OSError both times (the player shows "search failed" for it)."""
    lrclib.status = 500
    with pytest.raises(OSError):
        search_lrclib("x")
    monkeypatch.setenv("ICEPLAYER_LRCLIB_URL", "http://127.0.0.1:9/api/search")
    with pytest.raises(OSError):
        search_lrclib("x", timeout=3)


def test_save_lrc_for_writes_loadable_file(tmp_path):
    """Input: song.wav + downloaded text. Expected: song.lrc in UTF-8 that load_lrc_for
    reads back; a second save never overwrites it."""
    song = tmp_path / "晴天.wav"
    assert save_lrc_for(str(song), SUNNY)
    assert (tmp_path / "晴天.lrc").read_text(encoding="utf-8") == SUNNY
    assert load_lrc_for(str(song)) == [(28900, "故事的小黃花"), (32730, "從出生那年就飄著")]
    assert not save_lrc_for(str(song), LIVE)
    assert (tmp_path / "晴天.lrc").read_text(encoding="utf-8") == SUNNY


def test_save_lrc_for_unwritable_folder(tmp_path):
    """Input: a song in a folder that doesn't exist (stands in for read-only).
    Expected: False, no exception."""
    assert save_lrc_for(str(tmp_path / "missing" / "s.wav"), SUNNY) is False


def test_search_lrclib_logs_url_and_results(lrclib, caplog):
    """The console shows the search: title/artist, the address (readable and
    URL-encoded), the server's answer, each result, and which one was chosen.

    Input: 晴天 / 周杰伦 (270 s); lrclib returns a live version without times and the
    album version. Expected messages for all of these."""
    import logging
    caplog.set_level(logging.INFO, logger="iceplayer.lyrics")
    lrclib.results = [
        {"id": 1, "trackName": "晴天", "artistName": "周杰伦", "albumName": "Live",
         "duration": 299.0, "syncedLyrics": None},
        {"id": 2, "trackName": "晴天", "artistName": "周杰伦", "albumName": "叶惠美",
         "duration": 270.0, "syncedLyrics": SUNNY}]
    search_lrclib("晴天", "周杰伦", 270_000)
    text = caplog.text
    assert "搜索: 歌名=晴天  歌手=周杰伦" in text
    assert "?track_name=晴天&artist_name=周杰伦" in text                  # readable
    assert "track_name=%E6%99%B4%E5%A4%A9" in text                       # as sent
    assert "HTTP 200" in text
    assert "2 个结果，其中 1 个带时间轴" in text
    assert "1. id=1  周杰伦 - 晴天  《Live》  299.0 秒  无时间轴" in text
    assert "2. id=2  周杰伦 - 晴天  《叶惠美》  270.0 秒  带时间轴" in text
    assert "选中 id=2" in text and "歌曲 270 秒" in text


def test_search_log_lists_at_most_ten_results(lrclib, caplog):
    """Input: 25 results. Expected: 10 listed, then "... 还有 15 个"."""
    import logging
    caplog.set_level(logging.INFO, logger="iceplayer.lyrics")
    lrclib.results = [{"id": i, "syncedLyrics": SUNNY} for i in range(25)]
    search_lrclib("x")
    assert "10. id=9" in caplog.text and "11. id=10" not in caplog.text
    assert "还有 15 个" in caplog.text


# --------------------------------------------------- title-only fallback search
def _only_without_artist(results):
    """A fake-server answer: nothing for title + artist, ``results`` for title only."""
    return lambda query: [] if "artist_name" in query else results


def test_search_falls_back_to_title_only(lrclib, caplog):
    """Title + artist finds nothing (e.g. the tag says "Jay Chou", lrclib has 周杰伦).
    Expected: a second search with only the title, whose lyrics are returned, and a
    console line saying so."""
    import logging
    caplog.set_level(logging.INFO, logger="iceplayer.lyrics")
    lrclib.respond = _only_without_artist([{"duration": 270.0, "syncedLyrics": SUNNY}])
    assert search_lrclib("晴天", "Jay Chou", 270_000) == SUNNY
    assert [r.get("artist_name") for r in lrclib.requests] == ["Jay Chou", None]
    assert lrclib.requests[1]["track_name"] == "晴天"
    assert "歌名+歌手没有找到，改为只搜索歌名: 晴天" in caplog.text


def test_no_fallback_when_title_and_artist_find_lyrics(lrclib):
    """Title + artist already finds lyrics: only one request."""
    lrclib.results = [{"syncedLyrics": SUNNY}]
    assert search_lrclib("晴天", "周杰伦") == SUNNY
    assert len(lrclib.requests) == 1


def test_no_fallback_without_artist(lrclib):
    """No artist: the first search already was title-only, so it isn't repeated."""
    assert search_lrclib("晴天") is None
    assert len(lrclib.requests) == 1


def test_no_fallback_after_an_error(lrclib):
    """The first request fails (HTTP 500): the error is raised at once (the player
    shows "search failed"), no second request."""
    lrclib.status = 500
    with pytest.raises(OSError):
        search_lrclib("晴天", "周杰伦")
    assert len(lrclib.requests) == 1


@pytest.mark.parametrize("found_seconds, accepted", [(270.0, True), (279.0, True),
                                                     (281.0, False), (200.0, False)])
def test_title_only_rejects_songs_of_another_length(lrclib, found_seconds, accepted):
    """A title-only search also finds other songs with the same name. For a 270 s song
    only results within 10 s are accepted: 270 / 279 s yes, 281 / 200 s no."""
    lrclib.respond = _only_without_artist([{"duration": found_seconds, "syncedLyrics": SUNNY}])
    assert (search_lrclib("晴天", "Someone", 270_000) == SUNNY) is accepted


def test_title_only_with_unknown_length_takes_first_result(lrclib):
    """Song length not known yet (0): the length check can't be done, lrclib's first
    timed result is used."""
    lrclib.respond = _only_without_artist([{"duration": 100.0, "syncedLyrics": LIVE},
                                           {"duration": 270.0, "syncedLyrics": SUNNY}])
    assert search_lrclib("晴天", "Someone", 0) == LIVE


def test_pick_lyrics_max_diff(caplog):
    """pick_lyrics with max_diff_ms: too long/short results are dropped (and the
    console says how many); without a song length nothing is dropped."""
    import logging
    caplog.set_level(logging.INFO, logger="iceplayer.lyrics")
    results = [{"duration": 200.0, "syncedLyrics": LIVE}, {"duration": 268.0, "syncedLyrics": SUNNY}]
    assert pick_lyrics(results, 270_000, max_diff_ms=10_000) == SUNNY
    assert "排除 1 个" in caplog.text
    assert pick_lyrics(results[:1], 270_000, max_diff_ms=10_000) is None
    assert pick_lyrics(results[:1], 0, max_diff_ms=10_000) == LIVE
