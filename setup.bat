@echo off
REM One-time setup: creates a private Python environment (.venv) for this project
REM and installs the scout plus its optional browser and TLS extras into it.
cd /d "%~dp0"

if not exist .venv (
    echo Creating .venv ...
    python -m venv .venv || (echo Could not create the venv - is Python on PATH? & exit /b 1)
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .[browser,tls]
python -m playwright install chromium

echo.
echo Done. Double-click scout.bat to scout a site.
