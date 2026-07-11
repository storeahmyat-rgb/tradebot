#!/bin/bash
# Binance Futures Bot - Linux/Mac/Railway launcher
cd "$(dirname "$0")"

echo "================================"
echo " Binance Futures Bot - Starting"
echo "================================"

# Activate venv if exists (local dev only — Railway uses global pip)
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

# Railway injects PORT. Default to 5000 for local dev.
export PORT=${PORT:-5000}
export HOST=${HOST:-0.0.0.0}

echo "Starting web server at http://${HOST}:${PORT}"
echo "Press Ctrl+C to stop"
echo "================================"
python app.py
