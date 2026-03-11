@echo off
REM HiDock Background Watcher — monitors for completed recordings and downloads them.
REM Runs persistently. Close the window or press Ctrl+C to stop.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python watcher.py %*
