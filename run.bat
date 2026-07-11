@echo off
REM Binance Futures Bot - Windows launcher
cd /d "%~dp0"

echo ================================
echo  Binance Futures Bot - Starting
echo ================================

REM Activate venv if exists
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Check dependencies
python -c "import flask, flask_socketio, pandas, numpy" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

REM Start the bot
echo Starting web server at http://localhost:5000
echo Press Ctrl+C to stop
echo ================================
python app.py

pause
