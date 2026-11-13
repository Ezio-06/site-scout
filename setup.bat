@echo off
REM One-time setup: creates a private Python environment (.venv) for this project
REM and installs everything the scout needs into it - nothing touches your global Python.
cd /d "%~dp0"

if not exist .venv (
    echo Creating .venv ...
    python -m venv .venv || (echo Could not create the venv - is Python on PATH? & exit /b 1)
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium

echo.
echo Done. Each new cmd window, turn the environment on with:
echo     .venv\Scripts\activate
echo then run:  python run_scout.py https://example.com
