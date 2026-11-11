"""Launch IcePlayer:  python run.py [song.mp3]

Input: optional song path on the command line (play it, or hand it to a player that is
already running). Output: exits with ``iceplayer.app.main()``'s exit code.
Also the entry script PyInstaller packages into IcePlayer.exe.
"""
import sys

from iceplayer.app import main

if __name__ == "__main__":
    sys.exit(main())
