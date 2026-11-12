"""Read a song's title / artist / album with mutagen, repairing old Chinese/Japanese tags.

* ``read_tags``          title, artist, album of a file (any format mutagen knows).
* ``decode_legacy``      decode a file's Latin-1-marked ID3 texts, choosing ONE encoding
                         for the whole file.
* ``fallback_encoding``  which legacy code page to try (system default, or
                         ``ICEPLAYER_TAG_ENCODING``).

Why: ID3 text frames say how they are encoded (Latin-1, UTF-16 or UTF-8). Old Chinese
and Japanese taggers wrote GBK / Big5 / Shift-JIS bytes but marked them "Latin-1", so
Qt (and any tag reader that trusts the mark) shows 松田聖子 as "ËÉÌïÂ}×Ó". UTF-16 /
UTF-8 frames are correct and used as they are.

A few bytes such as ``CB C9 CC EF`` are valid GBK, Big5 *and* Latin-1, so no detector
can tell which was meant from one short tag (charset-normalizer reads GBK 刘惜君 as Big5
"隸洇澱"). Like foobar2000 / MusicBee, a fallback code page is used instead, and the
decision is made per file from all its Latin-1 tags together, which gives far more
evidence than a single tag.
"""
import ctypes
import os
import re
from dataclasses import dataclass

import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError

# Characters that normally appear in tags: Latin, punctuation, CJK symbols, kana, CJK,
# hangul, full-width forms. Anything else in a decoded result means "wrong encoding".
_UNUSUAL = re.compile(r"[^\x00-ɏ -⁯℀-⅏　-ヿ一-鿿"
                      r"가-힯＀-￯]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")         # hiragana / katakana
_CJK_LETTERS = re.compile(r"[ぁ-ヿ一-鿿가-힯]")
# A double-byte character whose two bytes are both >= 0xA1: the GB2312 / Big5 range
# that holds all common Chinese characters. Real Latin-1 text ("Sigur Rós": F3 73)
# never has two such bytes in a row, so this is the evidence needed for GBK / Big5.
_HIGH_PAIR = re.compile(rb"[\xa1-\xfe][\xa1-\xfe]")

# Windows ANSI code page -> Python codec for old tags.
_CODE_PAGES = {936: "gb18030", 950: "big5", 932: "cp932", 949: "cp949"}
_DOUBLE_BYTE_HAN = {"gb18030", "gbk", "gb2312", "big5", "cp950"}

# ID3 frame for each field; album artist (TPE2) stands in for a missing artist.
_ID3_FRAMES = {"title": ("TIT2",), "artist": ("TPE1", "TPE2"), "album": ("TALB",)}
# Other formats (FLAC, Ogg, M4A, WMA...): mutagen's "easy" keys, plus WMA names.
_EASY_KEYS = {"title": ("title", "Title"), "artist": ("artist", "Author", "albumartist"),
              "album": ("album", "WM/AlbumTitle")}


@dataclass
class Tags:
    """A song's text tags; "" for a missing one. Several artists are joined with "/"."""
    title: str = ""
    artist: str = ""
    album: str = ""


def fallback_encoding() -> str:
    """The legacy code page to decode old Latin-1-marked tags with.

    Returns:
        ``ICEPLAYER_TAG_ENCODING`` if set (e.g. "big5", "cp932"); otherwise the code page
        of a Chinese / Japanese / Korean Windows ("gb18030" on Simplified Chinese);
        otherwise "gb18030", since most such files come from Chinese tools.
    """
    forced = os.environ.get("ICEPLAYER_TAG_ENCODING", "").strip()
    if forced:
        return forced
    try:
        acp = ctypes.windll.kernel32.GetACP()          # Windows only
    except (AttributeError, OSError):
        acp = 0
    return _CODE_PAGES.get(acp, "gb18030")


def _try(raws: list[bytes], encoding: str) -> list[str] | None:
    """Decode every byte string with ``encoding``; None if any fails or looks wrong."""
    try:
        texts = [r.decode(encoding) for r in raws]
    except (UnicodeDecodeError, LookupError):
        return None
    return None if any(_UNUSUAL.search(t) for t in texts) else texts


def decode_legacy(raws: list[bytes], fallback: str | None = None) -> list[str]:
    """Decode all Latin-1-marked texts of ONE file with the encoding that fits them all.

    Args:
        raws:     The raw bytes of each such tag, e.g. title, artist, album.
        fallback: Legacy code page to use (default: ``fallback_encoding()``).

    Returns:
        The texts, in the same order. The first rule that works decides:

        1. All ASCII: unchanged.
        2. Valid UTF-8 (some tools write UTF-8 but mark it Latin-1).
        3. Shift-JIS (cp932), if it decodes cleanly and gives kana (only Japanese has
           kana, so this beats GBK even on a Chinese system).
        4. The fallback code page, if it decodes cleanly and gives CJK text. For GBK /
           Big5 at least one character must have both bytes >= 0xA1 (where all common
           characters are), so real Latin-1 names such as "Sigur Rós" are not taken
           for Chinese. One rare character like 聖 (GBK C2 7D) is fine when the file
           has other evidence.
        5. Otherwise Latin-1, i.e. as the tag says.
    """
    if all(r.isascii() for r in raws):
        return [r.decode("ascii") for r in raws]
    evidence = b"".join(r for r in raws if not r.isascii())
    utf8 = _try(raws, "utf-8")
    if utf8 is not None:
        return utf8
    japanese = _try(raws, "cp932")
    if japanese is not None and any(_KANA.search(t) for t in japanese):
        return japanese
    fallback = fallback or fallback_encoding()
    texts = _try(raws, fallback)
    if texts is not None and any(_CJK_LETTERS.search(t) for t in texts):
        if fallback.lower() not in _DOUBLE_BYTE_HAN or _HIGH_PAIR.search(evidence):
            return texts
    return [r.decode("latin-1") for r in raws]


def _id3_tags(path: str) -> Tags | None:
    """Tags from an ID3 block (MP3, also WAV/AIFF with ID3), or None if there is none."""
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        return None
    chosen = {}
    for field, frame_ids in _ID3_FRAMES.items():
        for fid in frame_ids:
            frame = id3.get(fid)
            if frame is not None and any(str(t).strip() for t in frame.text):
                chosen[field] = frame
                break
    # Old taggers: Latin-1-marked frames hold legacy bytes; decide for all of them at once.
    legacy = [f for f in chosen if chosen[f].encoding == 0]
    raws = [b"\0".join(str(t).encode("latin-1", "replace") for t in chosen[f].text)
            for f in legacy]
    decoded = dict(zip(legacy, decode_legacy(raws))) if legacy else {}
    values = {}
    for field, frame in chosen.items():
        text = decoded[field].split("\0") if field in decoded else [str(t) for t in frame.text]
        values[field] = "/".join(t.strip() for t in text if t.strip())
    return Tags(**values)


def read_tags(path: str) -> Tags:
    """Read a song's title, artist and album.

    Args:
        path: The song file (MP3, FLAC, OGG, M4A, WMA, WAV...).

    Returns:
        ``Tags``; fields the file doesn't have (or a file mutagen can't read) are "".
        MP3 / ID3 texts get the legacy-encoding repair of ``decode_legacy``; other
        formats store Unicode and are used as they are.
    """
    try:
        tags = _id3_tags(path)
        if tags is not None and (tags.title or tags.artist or tags.album):
            return tags
        audio = mutagen.File(path, easy=True)
    except Exception:          # damaged / unknown file: no tags, never crash the player
        return Tags()
    if audio is None or not audio.tags:
        return tags or Tags()
    values = {}
    for field, keys in _EASY_KEYS.items():
        for key in keys:
            try:
                items = audio.tags.get(key)
            except (KeyError, ValueError):
                items = None
            texts = [str(i).strip() for i in (items or []) if str(i).strip()]
            if texts:
                values[field] = "/".join(texts)
                break
    return Tags(**values)
