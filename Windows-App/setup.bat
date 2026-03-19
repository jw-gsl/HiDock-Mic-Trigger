@echo off
setlocal enabledelayedexpansion
echo === HiDock Windows App Setup ===
echo.

:: Check if Python is available
where python >nul 2>&1
if errorlevel 1 goto :install_python
goto :python_ready

:install_python
echo Python not found. Installing automatically...
echo.

set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if "%PROCESSOR_ARCHITECTURE%"=="ARM64" set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-arm64.exe

echo Downloading Python from %PYTHON_URL%...
curl -L -o "%TEMP%\python-installer.exe" %PYTHON_URL%
if errorlevel 1 (
    echo ERROR: Failed to download Python.
    pause
    exit /b 1
)

echo Installing Python...
"%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
if errorlevel 1 (
    echo ERROR: Python installation failed. Try running as Administrator.
    pause
    exit /b 1
)

del "%TEMP%\python-installer.exe" 2>nul
set "PATH=%LOCALAPPDATA%\Programs\Python\Python311\;%LOCALAPPDATA%\Programs\Python\Python311\Scripts\;%PATH%"

where python >nul 2>&1
if errorlevel 1 (
    echo Python installed but not found in PATH.
    echo Close this window, open a new Command Prompt, and run setup.bat again.
    pause
    exit /b 1
)

echo Python installed successfully.
echo.

:python_ready
python --version

echo.
echo Creating virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)

echo Activating venv...
call .venv\Scripts\activate.bat

echo.
echo Upgrading pip...
python -m pip install --upgrade pip

echo.
echo Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo === Setup complete ===
echo Run the app with: run.bat
echo.
echo NOTE: The speech recognition model (~550 MB) will be downloaded
echo from within the app when you click "Download Model".
pause
