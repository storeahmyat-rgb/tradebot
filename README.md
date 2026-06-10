# Crypto Futures Trading Bot

A modular crypto trading bot built with Python, CCXT, Streamlit, and a strategy that detects support/resistance breakouts and range reversals.

## Features
- Support / Resistance detection from recent swing highs/lows
- Breakout trading on strong volume
- Range trading with bullish/bearish reversal pattern checks
- Binance Futures and Weex Futures support via CCXT
- Secure API key input through Streamlit UI
- Leverage and trade amount configuration
- Automatic stop-loss and take-profit estimation

## Files
- `exchange_manager.py` - exchange connection, leverage, order execution, and balance checking
- `strategy.py` - S/R detection, breakout/range logic, candle pattern detection
- `ui.py` - Streamlit interface, bot lifecycle, logging
- `main.py` - entry point for Streamlit
- `requirements.txt` - required Python packages

## Installation
1. Create a Python 3.10+ virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the bot UI

```bash
streamlit run main.py
```

## How to use
1. Open the Streamlit app in your browser.
2. Select the exchange: `Binance` or `Weex`.
3. Enter your API key and secret key.
4. Enter your trading pair, timeframe, leverage, and trade amount.
5. Click `Start Bot`.
6. Monitor log messages in the app.

## Notes
- This project is a demonstration. Backtest and validate strategies before using real funds.
- If bracket orders are not supported for the selected exchange, the bot will still execute the market entry order and log the trade details.

## Troubleshooting
- Invalid API keys: The bot will log authentication failures during initialization.
- Network issues: The bot retries after brief delays and logs errors.
- Insufficient balance: CCXT exceptions are surfaced in the log area.
