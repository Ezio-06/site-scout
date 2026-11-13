@echo off
REM Asks for a web address, scouts it, and shows where the scout card is.
cd /d "%~dp0"
if not exist .venv (
    echo Run setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
echo.
set /p URL=Paste the web address to scout and press Enter: 
if "%URL%"=="" exit /b 0
python run_scout.py "%URL%"
echo.
pause
