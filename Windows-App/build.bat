@echo off
echo === Building HiDock.exe ===
echo.

call .venv\Scripts\activate.bat

pyinstaller hidock.spec --noconfirm

echo.
if exist dist\HiDock.exe (
    echo Build complete: dist\HiDock.exe
    dir dist\HiDock.exe
) else (
    echo Build failed. Check output above for errors.
)
pause
