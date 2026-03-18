@echo off
echo === HiDock Windows App Setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

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
echo Installing dependencies (this may take several minutes for PyTorch)...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo === Setup complete ===
echo Run the app with: run.bat
echo Build .exe with:  build.bat
pause
