@echo off
REM HiDock USB Extractor - Download new recordings
REM Connects to HiDock over USB and downloads any recordings not yet saved locally.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python extractor.py download-new
if %errorlevel% neq 0 (
    echo.
    echo Extractor exited with an error. Check that:
    echo   - HiDock is plugged in via USB
    echo   - WinUSB driver is installed (use Zadig)
    echo   - No other application is using the device
)
pause
