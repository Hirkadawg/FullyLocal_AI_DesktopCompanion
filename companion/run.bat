@echo off
REM Double-click launcher. Runs the companion in its own virtual environment,
REM so there is nothing to activate and nothing to remember.
REM Any arguments are passed straight through, e.g.:  run.bat --list-monitors

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Virtual environment not found.
    echo   Create it with:  python -m venv .venv
    echo   then:            .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py %*

echo.
echo   Companion has stopped. Nothing is running in the background now
echo   except Ollama itself ^(system tray^). Safe to close this window.
pause
