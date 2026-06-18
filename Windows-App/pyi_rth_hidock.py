"""PyInstaller runtime hook for HiDock.exe.

The app imports the cross-platform `shared/` package (and shells out to
`transcription-pipeline/`) which live at the repo root in dev. Both are bundled
into the frozen app as data trees (see hidock.spec), so at runtime they sit
under sys._MEIPASS. Put that on sys.path so the app's lazy `import shared.*`
calls resolve inside the frozen exe exactly as they do from source.
"""
import sys

if hasattr(sys, "_MEIPASS"):
    if sys._MEIPASS not in sys.path:
        sys.path.insert(0, sys._MEIPASS)
