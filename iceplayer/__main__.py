"""Lets the package be started with ``python -m iceplayer [song.mp3]`` (what VS Code's F5 runs).

Input: optional song path on the command line. Output: exits with ``main()``'s exit code.
"""
import sys

from .app import main

sys.exit(main())
