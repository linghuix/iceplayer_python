"""Shared test setup, loaded automatically by pytest before any test file.

What lives here
---------------
* Environment setup: Qt is switched to the "offscreen" platform so tests never open
  real windows on your screen, and noisy Qt debug logging is turned off.
* Fixtures (pytest's reusable test ingredients, requested by naming them as test
  function arguments):
    - ``qapp``      one QApplication shared by the whole run (Qt needs exactly one).
    - ``music_dir`` a temporary folder with three generated songs, one .lrc lyric file
                    and one non-audio file.
    - ``songs``     the three song paths from ``music_dir``, in a fixed order.
    - ``player``    a fresh main window (IcePlayer) using a temporary playlist file,
                    cleaned up after each test.
    - ``lrclib``    a fake lrclib.net lyric server on this computer (see below).
* Helpers imported by the test files:
    - ``process(ms)``          let Qt handle events for ``ms`` milliseconds.
    - ``wait_until(cond, ms)`` keep handling events until ``cond()`` is true or time runs out.
    - ``make_wav(path, secs)`` write a short, quiet sine-tone WAV file.
    - ``make_mp3(path, ...)``  write a silent MP3, optionally with ID3 tags, an embedded
                               cover and a LAME header (like real music-store files).
    - ``make_jpeg(w, h)``      a small JPEG image, used as an embedded cover.

Why the helpers exist
---------------------
Qt does its work (signals, timers, audio playback) inside an event loop. A test that
starts playback and immediately checks the result would see nothing yet, so tests call
``wait_until(...)`` to pump the event loop until the expected state appears.

Nothing here touches your real playlist (IceList.json): every player gets its own
file inside pytest's ``tmp_path`` folder, which is deleted afterwards.

No test uses the internet: the online lyric search is pointed at a closed local port
(so any accidental search fails at once), and tests that need answers use the
``lrclib`` fixture's fake server.
"""
import math
import os
import struct
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

# Must be set before PySide6 is imported: draw windows in memory, not on screen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.multimedia.*=false")
# Never search lyrics on the real lrclib.net from tests (also inherited by the
# run.py / exe processes some tests start). Port 9 is closed: a search fails at once.
os.environ["ICEPLAYER_LRCLIB_URL"] = "http://127.0.0.1:9/api/search"

# Make "import iceplayer" work no matter which folder pytest was started from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """The single QApplication for the whole test run.

    Also installs a safety net: every 300 ms it closes any popup menu or file dialog
    that a test opened by mistake. Without this, a real ``QMenu.exec()`` would wait
    forever for a mouse click that never comes and the whole run would hang.
    """
    app = QApplication.instance() or QApplication([])
    guard = QTimer()
    guard.timeout.connect(_close_blocking_windows)
    guard.start(300)
    yield app
    guard.stop()


def _close_blocking_windows():
    """Close a stray popup menu or file dialog (see ``qapp``)."""
    popup = QApplication.activePopupWidget()
    if popup:
        popup.close()
    modal = QApplication.activeModalWidget()
    if modal is not None and type(modal).__name__ == "QFileDialog":
        modal.reject()


def process(ms: int = 0):
    """Run the Qt event loop for ``ms`` milliseconds (0 = handle what is queued now)."""
    end = time.monotonic() + ms / 1000
    QApplication.processEvents()
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout_ms: int = 5000) -> bool:
    """Pump the event loop until ``predicate()`` is true; return False on timeout.

    Used as ``assert wait_until(lambda: player.is_playing())`` so a test waits for
    asynchronous things (audio starting, a song ending) without fixed sleeps.
    """
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_wav(path: Path, seconds: float = 1.0, rate: int = 22050):
    """Write a quiet stereo 440 Hz sine tone, so tests have real audio to play."""
    frames = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            s = int(1000 * math.sin(2 * math.pi * 440 * i / rate))
            data += struct.pack("<hh", s, s)
        w.writeframes(bytes(data))
    return path


def _id3_frame(frame_id: str, payload: bytes) -> bytes:
    """One ID3v2.3 frame: 4-letter id, 4-byte big-endian size, 2 flag bytes, payload."""
    return frame_id.encode("ascii") + struct.pack(">I", len(payload)) + b"\0\0" + payload


def _id3_text(frame_id: str, text: str, gbk: bool = False) -> bytes:
    """An ID3v2.3 text frame (TIT2 title, TPE1 artist, TALB album) in UTF-16, or with
    ``gbk=True`` as GBK bytes marked Latin-1 (encoding byte 0), like many old Chinese
    MP3s; Qt then shows 我很快乐 as "ÎÒºÜ¿ìÀÖ"."""
    if gbk:
        return _id3_frame(frame_id, b"\x00" + text.encode("gbk"))
    return _id3_frame(frame_id, b"\x01" + text.encode("utf-16"))


_MP3_HEADER = b"\xff\xfb\x90\x64"   # MPEG-1 Layer III, 128 kb/s, 44.1 kHz, joint stereo
_MP3_FRAME_BYTES = 417              # 144 * 128000 / 44100, no padding
_SAMPLES_PER_FRAME = 1152


def _crc16_lame(data: bytes) -> int:
    """CRC-16 (polynomial 0x8005, reflected, start 0) as used in the LAME tag."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _lame_info_frame(audio_frames: int, delay: int = 576, padding: int = 732) -> bytes:
    """Build the "Info" + LAME header frame that the LAME encoder writes first.

    It decodes to nothing; it only describes the file. FFmpeg reads the encoder
    ``delay`` (silent samples at the start) and ``padding`` (at the end) from it and
    skips them — that is what prints "Could not update timestamps for skipped samples".

    Layout (MPEG-1 stereo): 4-byte header, 32 bytes side info, then at offset 36 the
    Xing/Info block (flags, frame count, byte count, 100-byte seek table, quality),
    then at offset 156 the 36-byte LAME block ending in a CRC of bytes 0-189.
    """
    total_frames = audio_frames + 1
    info = (b"Info" + struct.pack(">I", 0x0F)                      # all 4 fields present
            + struct.pack(">I", total_frames)
            + struct.pack(">I", total_frames * _MP3_FRAME_BYTES)
            + bytes(int(i * 256 / 100) for i in range(100))          # linear seek table
            + struct.pack(">I", 0))                                  # quality
    lame = (b"LAME3.99r"
            + b"\x00"                     # tag revision / VBR method (CBR)
            + b"\x00"                     # lowpass
            + bytes(4) + bytes(2) + bytes(2)   # peak + 2 replay gains (unset)
            + b"\x00"                     # encoding flags / ATH
            + b"\x80"                     # bitrate 128
            + bytes([delay >> 4, ((delay & 0xF) << 4) | (padding >> 8), padding & 0xFF])
            + b"\x00" + b"\x00" + b"\x00\x00"  # misc, mp3 gain, preset/surround
            + struct.pack(">I", total_frames * _MP3_FRAME_BYTES)   # music length
            + b"\x00\x00")                # music CRC (not checked)
    frame = bytearray(_MP3_HEADER + bytes(32) + info + lame)
    frame += struct.pack(">H", _crc16_lame(bytes(frame[:190])))   # LAME tag CRC
    frame += bytes(_MP3_FRAME_BYTES - len(frame))
    return bytes(frame)


def make_mp3(path: Path, seconds: float = 2.0, title: str | None = None,
             artist: str | None = None, album: str | None = None,
             cover: bytes | None = None, lame_header: bool = False, gbk_tags: bool = False):
    """Write a real, playable MP3 file (silent audio), optionally with ID3 tags, an
    embedded cover picture and a LAME header, without needing any MP3 encoder.

    How: an MPEG-1 Layer III frame whose side information is all zeros decodes to
    silence, so the audio is just repeated 417-byte frames (128 kb/s, 44.1 kHz,
    ~26 ms each) behind a valid frame header. The tags go in an ID3v2.3 block at the
    start of the file, exactly where taggers such as foobar2000 or Mp3tag put them.

    Args:
        path:    File to create.
        seconds: Length of the audio.
        title / artist / album: Text tags (omitted when None).
        cover:   JPEG bytes stored as an APIC "front cover" frame — the "attached pic"
                 that FFmpeg reports as ``Stream #0:1: Video: mjpeg``.
        lame_header: Start the audio with a LAME "Info" frame (encoder delay 576),
                 like files from iTunes, foobar2000 or most music stores.
                 cover + lame_header together froze playback at 0:00 on Qt 6.8.
        gbk_tags: Write the text tags as GBK bytes marked Latin-1 (see ``_id3_text``).

    Returns:
        ``path``.
    """
    frame = _MP3_HEADER + bytes(_MP3_FRAME_BYTES - 4)   # header + zeroed side info/data
    count = max(1, int(seconds * 44100 / _SAMPLES_PER_FRAME))
    audio = frame * count
    if lame_header:
        audio = _lame_info_frame(count) + audio
    frames = b""
    if title is not None:
        frames += _id3_text("TIT2", title, gbk_tags)
    if artist is not None:
        frames += _id3_text("TPE1", artist, gbk_tags)
    if album is not None:
        frames += _id3_text("TALB", album, gbk_tags)
    if cover is not None:
        # encoding(0) + MIME + NUL + picture type 3 (front cover) + empty description + data
        frames += _id3_frame("APIC", b"\x00image/jpeg\x00\x03\x00" + cover)
    tag = b""
    if frames:
        n = len(frames)                                # tag size is "syncsafe" (7 bits/byte)
        size = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])
        tag = b"ID3\x03\x00\x00" + size + frames
    path.write_bytes(tag + audio)
    return path


def make_jpeg(width: int = 64, height: int = 48, color: str = "red") -> bytes:
    """A small solid-colour JPEG image (bytes), used as an embedded album cover."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "JPG")
    return bytes(buf.data())


@pytest.fixture
def music_dir(tmp_path):
    """A temporary music folder:

    ============  ========  ===========================================
    alpha.wav     1.0 s     plain song, no lyrics
    测试歌曲.wav   1.5 s     Chinese file name, has 测试歌曲.lrc (GBK)
    gamma.wav     1.0 s     plain song
    notes.txt               not audio, must be ignored by folder/drop adds
    ============  ========  ===========================================

    The lyric file shows "第一行" from 0.00 s and "第二行" from 0.80 s.
    """
    d = tmp_path / "music"
    d.mkdir()
    make_wav(d / "alpha.wav", 1.0)
    make_wav(d / "测试歌曲.wav", 1.5)
    make_wav(d / "gamma.wav", 1.0)
    (d / "测试歌曲.lrc").write_text("[00:00.00]第一行\n[00:00.80]第二行\n", encoding="gbk")
    (d / "notes.txt").write_text("not music")
    return d


@pytest.fixture
def songs(music_dir):
    """The three song paths as strings: [alpha, 测试歌曲, gamma]."""
    return [str(music_dir / n) for n in ("alpha.wav", "测试歌曲.wav", "gamma.wav")]


@pytest.fixture
def player(qapp, tmp_path, monkeypatch):
    """A new IcePlayer main window for one test, saving to ``tmp_path/state.json``.

    ``closeEvent`` normally calls ``QApplication.quit()``; that is replaced with a
    no-op so closing a player inside a test doesn't end the shared QApplication.
    After the test, the song is unloaded and all windows are closed and deleted.
    """
    from iceplayer import player as player_mod
    monkeypatch.setattr(player_mod, "QApplication", SimpleNamespace(quit=lambda: None))
    p = player_mod.IcePlayer(tmp_path / "state.json")
    yield p
    p.unload()
    p.close()
    p.lyric_window.close()
    p.mini_window.close()
    p.deleteLater()
    process(20)


@pytest.fixture
def lrclib(monkeypatch):
    """A fake lrclib.net search server on 127.0.0.1, used instead of the real one.

    Yields an object with:
        results   list sent back as JSON for every search (default: empty list).
        respond   optional function(query dict) -> results, used instead of
                  ``results`` (e.g. answer only searches without an artist).
        status    HTTP status to answer with (default 200; e.g. 500 to fail).
        delay     seconds to wait before answering (default 0).
        requests  list of query dicts received, e.g. [{"track_name": "晴天", ...}].

    ``ICEPLAYER_LRCLIB_URL`` points at it during the test; it is stopped afterwards.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qsl, urlsplit

    state = SimpleNamespace(results=[], respond=None, status=200, delay=0.0, requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = dict(parse_qsl(urlsplit(self.path).query))
            state.requests.append(query | {"_ua": self.headers.get("User-Agent", "")})
            time.sleep(state.delay)
            results = state.respond(query) if state.respond else state.results
            body = json.dumps(results).encode("utf-8")
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass                       # keep pytest output clean

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ICEPLAYER_LRCLIB_URL", f"http://127.0.0.1:{server.server_port}/api/search")
    yield state
    server.shutdown()
    server.server_close()
