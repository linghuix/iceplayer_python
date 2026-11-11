"""Play-mode logic and playlist persistence (replaces Qt5's QMediaPlaylist, removed in Qt6).

Everything here is plain Python (no Qt), so it is easy to test and reuse:

* ``PlayMode`` / ``MODE_NAMES``  the four play modes and their Chinese menu labels.
* ``is_audio``                   decide whether a file is a song we can play.
* ``next_index`` / ``previous_index``
                                 choose which playlist row plays next.
* ``load_state`` / ``save_state`` read/write IceList.json (playlist, volume, mode).
"""
import json
import random
from enum import IntEnum
from pathlib import Path

#: File extensions treated as playable audio (lower case, with the dot).
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".opus"}

#: Menu labels for the play modes, indexed by ``PlayMode`` value.
MODE_NAMES = ["单曲播放", "列表循环", "单曲循环", "随机播放"]


class PlayMode(IntEnum):
    """What happens when a song finishes. Values are saved in IceList.json, so don't renumber."""

    SINGLE = 0        # 单曲播放: stop after the current song
    LIST_LOOP = 1     # 列表循环: play the next song, wrap from last to first
    SINGLE_LOOP = 2   # 单曲循环: repeat the current song
    RANDOM = 3        # 随机播放: play a random different song


def is_audio(path: str) -> bool:
    """Tell whether a file looks like a playable song, judging by its extension.

    Args:
        path: File name or full path, e.g. ``"D:/Music/song.MP3"``.

    Returns:
        True if the extension (case-insensitive) is in ``AUDIO_EXTENSIONS``.
        The file itself is not opened or checked for existence.
    """
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def next_index(current: int, count: int, mode: PlayMode, auto: bool) -> int | None:
    """Choose the playlist row to play after ``current``.

    Args:
        current: Row now playing, or -1 if nothing has played yet.
        count:   Number of songs in the playlist.
        mode:    The active ``PlayMode``.
        auto:    True when the song ended by itself; False when the user pressed Next.
                 (Next always moves on, even in 单曲播放 / 单曲循环.)

    Returns:
        The row to play next, or None to stop (empty list, or 单曲播放 at song end).
        In random mode the result is never ``current`` unless it is the only song.
    """
    if count == 0:
        return None
    if auto and mode == PlayMode.SINGLE:
        return None
    if auto and mode == PlayMode.SINGLE_LOOP:
        return current
    if mode == PlayMode.RANDOM and count > 1:
        return random.choice([i for i in range(count) if i != current])
    return (current + 1) % count


def previous_index(current: int, count: int, mode: PlayMode) -> int | None:
    """Choose the playlist row for the Previous button.

    Args:
        current: Row now playing (callers pass 0 when nothing has played).
        count:   Number of songs in the playlist.
        mode:    The active ``PlayMode``; random mode picks a random other song.

    Returns:
        The row to play, wrapping from the first song to the last, or None if the
        playlist is empty.
    """
    if count == 0:
        return None
    if mode == PlayMode.RANDOM and count > 1:
        return random.choice([i for i in range(count) if i != current])
    return (current - 1) % count


def load_state(path: Path) -> dict:
    """Read the saved playlist, volume and play mode.

    Args:
        path: The state file (normally IceList.json next to the app).

    Returns:
        A dict ``{"files": list[str], "volume": int 0-100, "mode": int 0-3}``.
        A missing, unreadable or corrupt file gives the defaults
        ``{"files": [], "volume": 100, "mode": 0}``; bad individual values are
        dropped (non-string paths), clamped (volume) or reset (unknown mode).
    """
    state = {"files": [], "volume": 100, "mode": int(PlayMode.SINGLE)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if isinstance(data, dict):
        state["files"] = [p for p in data.get("files", []) if isinstance(p, str)]
        if isinstance(data.get("volume"), int):
            state["volume"] = max(0, min(100, data["volume"]))
        if data.get("mode") in tuple(PlayMode):
            state["mode"] = int(data["mode"])
    return state


def save_state(path: Path, files: list[str], volume: int, mode: int):
    """Write the playlist, volume and play mode to the state file.

    Args:
        path:   Where to write (normally IceList.json).
        files:  Song paths in playlist order.
        volume: Volume 0-100.
        mode:   ``PlayMode`` value 0-3.

    Returns:
        None. Write errors (read-only folder, missing directory) are ignored so the
        player keeps running; the file is UTF-8 JSON with Chinese kept readable.
    """
    data = {"files": files, "volume": volume, "mode": int(mode)}
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
