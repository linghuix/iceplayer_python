"""Entry point: application start-up and the "only one player at a time" rule.

How the single-instance rule works:
1. On start, ``send_to_running_instance`` tries to connect to a local pipe named
   ``SERVER_NAME``. If a player is already listening there, the new launch sends it
   the file to play (one line of text), waits for the reply "ok", and exits.
2. Otherwise this launch becomes the player and calls ``start_server`` to listen on
   that pipe for later launches.
"""
import os
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .player import IcePlayer
from .widgets import FONT_FAMILY, res

def _app_dir() -> Path:
    """Find the folder the program lives in.

    Returns:
        The folder of IcePlayer.exe when running as a packaged .exe (PyInstaller sets
        ``sys.frozen``), otherwise the ``iceplayer_py`` folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _is_writable(folder: Path) -> bool:
    """Check whether files can be created in a folder (by really creating one, once).

    Deliberately not ``tempfile``: on Windows, ``tempfile`` treats "access denied" as
    "that name is taken" and retries with new names up to ``TMP_MAX`` (2,147,483,647)
    times, because ``os.access`` wrongly reports Program Files as writable. That made
    the installed IcePlayer.exe hang at start-up with no window.

    Args:
        folder: Folder to test.

    Returns:
        True if a uniquely named file could be created and removed there; False for
        read-only places such as ``C:\\Program Files`` without admin rights, or a
        folder that doesn't exist. Never retries, so it returns at once.
    """
    probe = folder / f".iceplayer-write-test-{uuid.uuid4().hex}"
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        pass
    return True


def default_state_file() -> Path:
    """Choose where the playlist file (IceList.json) is kept.

    Returns:
        * running from source: ``iceplayer_py/IceList.json``;
        * portable exe (zip, folder you can write to): next to IcePlayer.exe;
        * installed exe (MSI, in Program Files, not writable):
          ``%LOCALAPPDATA%\\IcePlayer\\IceList.json`` (the folder is created).
    """
    folder = _app_dir()
    if getattr(sys, "frozen", False) and not _is_writable(folder):
        folder = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "IcePlayer"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass   # save_state ignores write errors, so the player still runs
    return folder / "IceList.json"


# Both can be overridden (used by the tests so they never touch the real playlist).
SERVER_NAME = os.environ.get("ICEPLAYER_SERVER", "iceplayer-python")   # local pipe name
STATE_FILE = Path(os.environ.get("ICEPLAYER_STATE") or default_state_file())


ACK = b"ok"   # reply the running player sends after it has read a message


def send_to_running_instance(message: str) -> bool:
    """If another IcePlayer is running, hand it our file argument.

    The message is one newline-terminated line. We wait for the running instance to
    reply "ok" before disconnecting: on Windows, disconnecting straight after writing
    can drop the message before the other side has read it.

    Args:
        message: Song path to play, or "" to just bring the running window to the front.

    Returns:
        True if a player is already running (this launch should exit), False if none is
        running (this launch should open its own window). Connecting gives up after
        0.3 s; waiting for "ok" after 3 s.
    """
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write((message + "\n").encode("utf-8"))
    sock.flush()
    reply = b""
    while ACK not in reply and sock.waitForReadyRead(3000):
        reply += bytes(sock.readAll())
    sock.disconnectFromServer()
    return True  # an instance is running either way; never start a second one


def start_server(player: IcePlayer) -> QLocalServer:
    """Listen for later launches and pass their files to this player.

    Args:
        player: The main window. For each message it is brought to the front, and a
                non-empty path is opened and played with ``player.open_and_play``.

    Returns:
        The listening QLocalServer (owned by ``player``). A pipe left behind by a
        crashed instance is removed first.
    """
    server = QLocalServer(player)
    QLocalServer.removeServer(SERVER_NAME)  # clean up after a crashed instance

    def on_connection():
        """Server callback: serve every newly connected launch."""
        while server.hasPendingConnections():
            _serve(server.nextPendingConnection())

    def _serve(sock: QLocalSocket):
        """Read one message line from a connected launch, reply "ok" and act on it.

        Args:
            sock: The connection. Data may arrive in pieces, so it is buffered until
                  the newline; the socket deletes itself when the sender disconnects.
        """
        buffer = bytearray()

        def read():
            """Socket callback: collect data; once a full line is in, handle it once."""
            buffer.extend(bytes(sock.readAll()))
            if b"\n" not in buffer:
                return  # wait for the rest of the line
            sock.readyRead.disconnect(read)
            sock.write(ACK)
            sock.flush()
            path = bytes(buffer).split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
            player.bring_to_front()
            if path:
                player.open_and_play(path)

        sock.disconnected.connect(sock.deleteLater)
        sock.readyRead.connect(read)
        if sock.bytesAvailable():
            read()

    server.newConnection.connect(on_connection)
    server.listen(SERVER_NAME)
    return server


def use_light_colors(app: QApplication) -> bool:
    """Make the whole app use light colours, even when Windows is in dark mode.

    Qt 6 follows the Windows light/dark setting by default, so on a computer in dark
    mode the playlist, menus and dialogs got light text and dark or see-through
    backgrounds. The skin is designed for light colours only.

    Args:
        app: The running QApplication.

    Returns:
        True if the light scheme was set (Qt 6.8+), False on older Qt (the playlist's
        own explicit colours still apply then).
    """
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Light)
        return True
    return False


def main() -> int:
    """Start IcePlayer.

    Inputs (from the command line): ``sys.argv[1]``, optional path of a song to play.

    Returns:
        The process exit code: 0 if the file was handed to an already running player,
        otherwise the Qt event loop's exit code when the window is closed.
    """
    QCoreApplication.setApplicationName("IcePlayer")
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("冰点播放器")
    app.setStyle("Fusion")  # flat table rows like the original, not the rounded Win11 style
    use_light_colors(app)
    app.setFont(QFont(FONT_FAMILY, 9))
    icon = QIcon()
    for size in (16, 32, 48):
        icon.addFile(res(f"logo-{size}.png"))
    app.setWindowIcon(icon)

    file_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if send_to_running_instance(file_arg):
        return 0

    player = IcePlayer(STATE_FILE)
    start_server(player)
    player.show()
    if file_arg:
        player.open_and_play(file_arg)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
