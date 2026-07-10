#!/bin/bash
# Binance Futures Bot - Linux/Mac launcher
cd "$(dirname "$0")"

echo "================================"
echo " Binance Futures Bot - Starting"
echo "================================"

# Activate venv if exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Check dependencies
python -c "import flask, flask_socketio, pandas, numpy" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

# Start the bot
echo "Starting web server at http://localhost:5000"
echo "Press Ctrl+C to stop"
echo "================================"
python app.py
