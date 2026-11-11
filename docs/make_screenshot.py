"""Regenerate docs/screenshot.png, the picture at the top of README.md.

Usage (from the iceplayer_py folder, qtapp environment active):

    python docs/make_screenshot.py

It runs the real player with real fonts but without showing a window on screen
(Qt's WA_DontShowOnScreen), loads a few demo songs (silent MP3s with made-up titles,
generated on the fly), plays one and pauses it part-way, then saves the window
over a soft gradient, at the window's 90% opacity, like it looks on a desktop.
Nothing touches your real playlist: everything uses a temporary folder.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ.pop("QT_QPA_PLATFORM", None)                 # real platform = real fonts

from PySide6.QtCore import QPoint, Qt                   # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer          # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

OUTPUT = ROOT / "docs" / "screenshot.png"
DEMO_SONGS = [   # (file name shown in the list, title tag, artist tag, album tag)
    ("冰点 - 序曲", "冰点 · 序曲", "IcePlayer 演示", "冰点精选"),
    ("Morning Light - Demo Band", "Morning Light", "Demo Band", "Sunrise"),
    ("夏夜晚风 - 示例歌手", "夏夜晚风", "示例歌手", "夏日专辑"),
    ("Blue Horizon - Sample Artist", "Blue Horizon", "Sample Artist", "Skyline"),
    ("海边的旋律 - 演示乐队", "海边的旋律", "演示乐队", "海风"),
    ("City Lights (Live) - Example", "City Lights (Live)", "Example", "Live 2024"),
    ("雨夜 - 测试歌手", "雨夜", "测试歌手", "四季"),
]
PLAYING_ROW = 2             # 夏夜晚风
PAUSE_AT_MS = 83_000        # progress bar part-way: 01:23
MARGIN = 28                 # gradient border around the window, in pixels


def wait_until(app, predicate, seconds=10.0):
    """Let Qt run until ``predicate()`` is true (or time runs out)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def render_window(app, music: Path) -> QImage:
    """Fill a player with the demo songs, pause one part-way, and grab the window.

    Args:
        app:   The QApplication.
        music: Temporary folder for the generated songs and playlist file.

    Returns:
        The window as an image with transparency (device pixels).
    """
    from conftest import make_mp3
    from iceplayer import player as player_mod
    from iceplayer.app import use_light_colors

    use_light_colors(app)
    player = player_mod.IcePlayer(music / "IceList.json")
    player.setAttribute(Qt.WA_DontShowOnScreen, True)
    player.audio.setMuted(True)
    paths = [str(make_mp3(music / f"{name}.mp3", 150, title=title, artist=artist, album=album))
             for name, title, artist, album in DEMO_SONGS]
    player.add_files(paths)
    player.show()
    player.play_index(PLAYING_ROW)
    wait_until(app, lambda: player.media.duration() > 0 and player.is_playing())
    player.media.setPosition(PAUSE_AT_MS)
    wait_until(app, lambda: player.media.position() >= PAUSE_AT_MS)
    player.media.pause()
    wait_until(app, lambda: player.media.playbackState() == QMediaPlayer.PausedState)
    player.table.selectRow(PLAYING_ROW)
    wait_until(app, lambda: player.name_label.text() == DEMO_SONGS[PLAYING_ROW][1], 5)
    app.processEvents()
    image = player.grab().toImage()
    player.unload()
    player.hide()
    return image


def compose(window: QImage) -> QImage:
    """Place the window on a soft gradient at 90% opacity (like setWindowOpacity(0.9))."""
    ratio = window.devicePixelRatio()
    margin = int(MARGIN * ratio)
    canvas = QImage(window.width() + 2 * margin, window.height() + 2 * margin,
                    QImage.Format_ARGB32_Premultiplied)
    gradient = QLinearGradient(0, 0, canvas.width(), canvas.height())
    gradient.setColorAt(0.0, QColor("#cfe8ff"))
    gradient.setColorAt(1.0, QColor("#6fa8dc"))
    painter = QPainter(canvas)
    painter.fillRect(canvas.rect(), gradient)
    painter.setOpacity(0.9)
    window.setDevicePixelRatio(1.0)          # draw pixel-for-pixel
    painter.drawImage(QPoint(margin, margin), window)
    painter.end()
    return canvas


def main() -> int:
    """Generate docs/screenshot.png. Returns 0 on success."""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as tmp:
        window = render_window(app, Path(tmp))
    image = compose(window)
    OUTPUT.parent.mkdir(exist_ok=True)
    image.save(str(OUTPUT))
    print(f"saved {OUTPUT} ({image.width()}x{image.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
