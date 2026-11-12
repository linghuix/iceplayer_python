"""Tests for iceplayer/tags.py — reading title / artist / album with mutagen.

What is tested
--------------
1. ``decode_legacy``      Old tags whose bytes are GBK / Shift-JIS / Big5 / UTF-8 but are
                          marked Latin-1 (Qt shows 松田聖子 as "ËÉÌïÂ}×Ó"): one encoding
                          is chosen for all such tags of a file. Real Latin-1 names
                          ("Sigur Rós") must stay as they are.
2. ``fallback_encoding``  The legacy code page: system default or ICEPLAYER_TAG_ENCODING.
3. ``read_tags``          Real MP3 files: UTF-16 tags, legacy tags, several artists,
                          album artist instead of artist, files without tags, broken files.

How it is tested
----------------
Byte strings are built with ``"text".encode("gbk")`` etc., exactly what an old tagger
wrote. MP3 files are made with conftest's ``make_mp3`` (silent audio) and their ID3
frames are written with mutagen, including frames whose raw bytes are legacy text
marked Latin-1 (``encoding=0``), like the files that showed garbled artists.

How to run
----------
    python -m pytest tests/test_tags.py -v
"""
import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TPUB

from conftest import make_mp3, make_wav
from iceplayer.tags import Tags, decode_legacy, fallback_encoding, read_tags

# The tags of the reported file "松田聖子 - Rock’n Rouge.mp3" (all marked Latin-1):
# title with GBK ’ (A1 AF), artist 松田聖子 where 聖 is GBK C2 7D (second byte = "}"),
# and the publisher in GBK full-width letters.
ROUGE_TITLE = b"Rock\xa1\xafn Rouge"
ROUGE_ARTIST = b"\xcb\xc9\xcc\xef\xc2}\xd7\xd3"
ROUGE_PUBLISHER = b"\xa3\xc7\xa3\xd4\xa1\xa1\xa3\xed\xa3\xf5\xa3\xf3\xa3\xe9\xa3\xe3"


# =============================================================== 1. decode_legacy
@pytest.mark.parametrize("raws, expected", [
    ([b"Hello", b"Album"], ["Hello", "Album"]),                                  # ASCII
    (["我很快乐".encode("gbk"), "刘惜君".encode("gbk")], ["我很快乐", "刘惜君"]),  # GBK
    ([ROUGE_TITLE, ROUGE_ARTIST], ["Rock’n Rouge", "松田聖子"]),                   # the bug
    ([ROUGE_ARTIST], ["松田聖子"]),                                              # 聖 alone
    (["蔡依林,周杰伦".encode("gbk"), b"Pop"], ["蔡依林,周杰伦", "Pop"]),
    (["赤いスイートピー".encode("cp932"), "松田聖子".encode("cp932")],
     ["赤いスイートピー", "松田聖子"]),                                         # Shift-JIS
    (["Café".encode(), "晴天".encode()], ["Café", "晴天"]),                       # UTF-8
    ([b"Sigur R\xf3s", b"M\xf6tley Cr\xfce"], ["Sigur Rós", "Mötley Crüe"]),     # real Latin-1
    ([b"Beyonc\xe9"], ["Beyoncé"]),
], ids=["ascii", "gbk", "rouge-title+artist", "sei-alone", "gbk-comma", "shift-jis",
        "utf-8", "latin-1", "latin-1-short"])
def test_decode_legacy(raws, expected):
    """Input: raw bytes of a file's Latin-1-marked tags. Expected: the real text
    (with the default GBK fallback of a Chinese system)."""
    assert decode_legacy(raws, "gb18030") == expected


def test_decode_legacy_whole_file_gives_evidence():
    """The decision is made for the whole file: the reported file's title and
    publisher (common GBK characters) and artist together are GBK."""
    assert decode_legacy([ROUGE_TITLE, ROUGE_ARTIST, ROUGE_PUBLISHER], "gb18030") == \
        ["Rock’n Rouge", "松田聖子", "ＧＴ　ｍｕｓｉｃ"]


def test_decode_legacy_big5_needs_big5_fallback():
    """Big5 and GBK bytes overlap, so Big5 tags need ICEPLAYER_TAG_ENCODING=big5 (or a
    Traditional Chinese Windows). Input: 周杰倫 in Big5. Expected: right with "big5"."""
    raw = ["周杰倫".encode("big5"), "葉惠美".encode("big5")]
    assert decode_legacy(raw, "big5") == ["周杰倫", "葉惠美"]


def test_decode_legacy_kana_beats_gbk():
    """Japanese Shift-JIS tags on a Chinese system: kana prove it is Japanese, so it
    isn't read as GBK."""
    raws = ["ロックン・ルージュ".encode("cp932")]
    assert decode_legacy(raws, "gb18030") == ["ロックン・ルージュ"]


def test_decode_legacy_undecodable_stays_latin1():
    """Bytes that fit no encoding: shown as Latin-1 (as the tag says), never a crash."""
    assert decode_legacy([b"\xff\xfe\xfd"], "gb18030") == ["ÿþý"]


# =============================================================== 2. fallback_encoding
def test_fallback_encoding_env_override(monkeypatch):
    """ICEPLAYER_TAG_ENCODING wins (e.g. for an old Big5 collection)."""
    monkeypatch.setenv("ICEPLAYER_TAG_ENCODING", "big5")
    assert fallback_encoding() == "big5"


def test_fallback_encoding_default(monkeypatch):
    """Without the variable: a CJK system code page, or gb18030 by default."""
    monkeypatch.delenv("ICEPLAYER_TAG_ENCODING", raising=False)
    assert fallback_encoding() in ("gb18030", "big5", "cp932", "cp949")


# =============================================================== 3. read_tags
def _legacy_frame(cls, raw: bytes):
    """An ID3 text frame marked Latin-1 whose bytes are ``raw`` (mutagen writes Latin-1
    text byte for byte)."""
    return cls(encoding=0, text=[raw.decode("latin-1")])


def test_read_tags_utf16(tmp_path):
    """Normal tags (UTF-16, like conftest's make_mp3 writes) are read as they are."""
    song = make_mp3(tmp_path / "a.mp3", 1, title="晴天", artist="周杰伦", album="叶惠美")
    assert read_tags(str(song)) == Tags("晴天", "周杰伦", "叶惠美")


def test_read_tags_reported_file(tmp_path):
    """A copy of the reported file's tags (GBK marked Latin-1, 聖 = C2 7D).
    Expected: 松田聖子 / Rock’n Rouge / Tinker Bell."""
    song = make_mp3(tmp_path / "rouge.mp3", 1)
    id3 = ID3()
    id3.add(_legacy_frame(TIT2, ROUGE_TITLE))
    id3.add(_legacy_frame(TPE1, ROUGE_ARTIST))
    id3.add(_legacy_frame(TALB, b"Tinker Bell"))
    id3.add(_legacy_frame(TPUB, ROUGE_PUBLISHER))
    id3.save(str(song), v2_version=3)
    assert read_tags(str(song)) == Tags("Rock’n Rouge", "松田聖子", "Tinker Bell")


def test_read_tags_gbk_from_make_mp3(tmp_path):
    """conftest's gbk_tags MP3 (GBK marked Latin-1) reads correctly."""
    song = make_mp3(tmp_path / "g.mp3", 1, title="我很快乐", artist="刘惜君", album="专辑",
                    gbk_tags=True)
    assert read_tags(str(song)) == Tags("我很快乐", "刘惜君", "专辑")


def test_read_tags_several_artists(tmp_path):
    """An artist frame with several values (Qt keeps only the first): all of them,
    joined with "/"."""
    song = make_mp3(tmp_path / "m.mp3", 1)
    id3 = ID3()
    id3.add(TIT2(encoding=1, text=["快乐出发"]))
    id3.add(TPE1(encoding=1, text=["李宇春", "张杰", "苏醒"]))
    id3.save(str(song), v2_version=4)
    assert read_tags(str(song)).artist == "李宇春/张杰/苏醒"


def test_read_tags_album_artist_when_no_artist(tmp_path):
    """No artist frame: the album artist (TPE2) is used."""
    song = make_mp3(tmp_path / "aa.mp3", 1)
    id3 = ID3()
    id3.add(TPE2(encoding=1, text=["Various"]))
    id3.save(str(song))
    assert read_tags(str(song)) == Tags("", "Various", "")


@pytest.mark.parametrize("make", ["wav", "untagged-mp3", "not-audio", "missing", "broken-mp3"])
def test_read_tags_without_tags(tmp_path, make):
    """Files without tags, non-audio, missing or damaged files: empty Tags, no error."""
    path = tmp_path / "x"
    if make == "wav":
        path = tmp_path / "x.wav"
        make_wav(path, 0.2)
    elif make == "untagged-mp3":
        path = make_mp3(tmp_path / "x.mp3", 1)
    elif make == "not-audio":
        path = tmp_path / "x.txt"
        path.write_text("hello")
    elif make == "broken-mp3":
        path = tmp_path / "x.mp3"
        path.write_bytes(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f" + b"\x00" * 20)
    assert read_tags(str(path)) == Tags()
