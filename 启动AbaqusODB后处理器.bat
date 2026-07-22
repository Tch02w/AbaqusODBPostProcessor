@echo off
setlocal
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] Project virtual environment was not found.
    echo Expected: %CD%\.venv\Scripts\pythonw.exe
    pause
    exit /b 1
)

start "Abaqus ODB PostProcessor" ".venv\Scripts\pythonw.exe" -m abaqus_odb_postprocessor.app
exit /b 0
