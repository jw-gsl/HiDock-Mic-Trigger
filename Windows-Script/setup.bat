@echo off
REM HiDock USB Extractor - Windows Setup
REM Run this once to create the virtual environment and install dependencies.

echo === HiDock USB Extractor - Windows Setup ===
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv .venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo === Setup complete ===
echo.
echo IMPORTANT: You must install the WinUSB driver for your HiDock device.
echo   1. Download Zadig from https://zadig.akeo.ie/
echo   2. Plug in your HiDock
echo   3. In Zadig, select "HiDock_H1" from the device list
echo   4. Select "WinUSB" as the target driver
echo   5. Click "Replace Driver" or "Install Driver"
echo.
echo Then run: run.bat
pause
