@echo off
setlocal enabledelayedexpansion

REM Change to this script's directory
cd /d "%~dp0"

REM Activate virtual environment if present
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

REM Ensure app.py exists here
if not exist "app.py" (
  echo app.py not found in %cd%. Please cd into the project folder that contains app.py.
  exit /b 1
)

REM Run Flask via Python module so PATH to flask.exe isn't needed
python -m flask --app app run --debug --host 0.0.0.0 --port 5000
