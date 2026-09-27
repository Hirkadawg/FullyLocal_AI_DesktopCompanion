@echo off
REM Double-click launcher for the windowed companion.
REM Closing the window hides it to the tray; quit from the tray icon.

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo   Virtual environment not found.
    echo   Create it with:  python -m venv .venv
    echo   then:            .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM pythonw.exe, not python.exe: no console window tags along behind the app.
start "" ".venv\Scripts\pythonw.exe" main.py --gui
