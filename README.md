# IcePlayer (冰点播放器), Python + Qt

A Python / PySide6 (Qt 6) version of the C++/Qt5 IcePlayer in `../ICEPLAYER`.
It uses the same skin images, window layout and behaviour.

## Run

```bash
pip install -r requirements.txt
python run.py                 # or: python -m iceplayer
python run.py "D:/Music/song.mp3"
```

> Use PySide6 **6.10.2** (pinned in `requirements.txt`; the `qtapp` conda env has it).
>
> - **Not 6.8.x:** it can't play MP3s that have both an embedded cover picture and a
>   LAME header, which describes most store/iTunes files. The song "plays" at 0:00
>   with no sound.
> - **Not 6.12 on Miniconda base:** it fails with *"DLL load failed while importing
>   QtCore"* because that Python's MSVC runtime is too old.

## Features

- Main window (400×600, frameless, semi-transparent, drag to move): cover art, title
  (scrolls when long), artist, album, seek bar (seeks when you release it), time.
- Play/pause (pause fades out the volume), previous/next, volume popup, play-mode
  menu: 单曲播放 / 列表循环 / 单曲循环 / 随机播放. Space toggles play/pause.
- Playlist: double-click to play, right-click to add songs or a folder, remove one
  song or all. Drag files or folders onto the window. Missing files show "失效".
  Supports mp3, flac, wav, ogg, m4a, aac, wma, opus.
- Desktop lyrics: an always-on-top bar that reads `song.lrc` next to the song file.
  UTF-8, UTF-16 and GBK files are all supported. Right-click the bar to hide it.
- Cover art comes from `song.jpg` next to the song, then from art embedded in the
  file, then the default image.
- Mini mode: a 100×100 floating button. Right-click it for the menu; double-click
  it to go back to the main window.
- About dialog: click the logo.
- Single instance: running it again with a file plays that file in the open window.
- The playlist, volume and play mode are saved to `IceList.json`.

## Changes from the C++ version

- **Online lyrics/cover download removed.** The Baidu Music API it called no
  longer exists. Local `.lrc` and `.jpg` files, including ones the old version
  downloaded, still work.
- Qt 6 has no `QMediaPlaylist`, so play modes are handled in `playlist.py`.
- The playlist is now JSON (`IceList.json`) instead of the binary `IceList.ilst`.
- The app no longer registers itself as an `.mp3` handler in the registry. Use
  Windows "Open with" and point it at a launcher if you want that.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest            # 281 tests, about 30 s
python -m pytest tests/test_lyrics.py -v    # one file, verbose
```

The tests run Qt on the `offscreen` platform, so no windows appear. They play
short generated WAV files, so an audio output device is needed. They never touch
your real `IceList.json` and never talk to a player you have open.

| File | Covers |
|---|---|
| `tests/test_lyrics.py` | LRC encoding detection, time-tag parsing, lyric lookup, desktop lyric window |
| `tests/test_playlist.py` | audio file types, next/previous for every play mode, saving/loading state |
| `tests/test_widgets.py` | every skin image, hover buttons, scrolling title, volume popup, window dragging |
| `tests/test_player.py` | playlist add/remove/clear, dialogs, right-click menu, play/pause/fade, next/prev, end-of-song per mode, seeking, real-world MP3s (ID3 tags, embedded cover, LAME header), lyrics sync, cover art, drag & drop, saving on close |
| `tests/test_mini.py` | about dialog, mini window buttons and menu, syncing with the main window |
| `tests/test_app.py` | single-instance handoff from a second process, the real `run.py` / `python -m iceplayer` launchers |

## Layout

```
run.py                  launcher
iceplayer/app.py        startup + single-instance (QLocalServer)
iceplayer/player.py     main window and playback
iceplayer/playlist.py   play-mode logic, saving
iceplayer/lyrics.py     LRC parser + desktop lyric window
iceplayer/mini.py       mini window + about dialog
iceplayer/widgets.py    hover buttons, scrolling label, volume button, dragging
iceplayer/resources/    skin images (copied from ../ICEPLAYER/Resources)
```
