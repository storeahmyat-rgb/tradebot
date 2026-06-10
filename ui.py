import asyncio
import threading
import time
from datetime import datetime
from typing import Any, Dict

import streamlit as st

from exchange_manager import (
    build_exchange,
    calculate_position_quantity,
    create_bracket_orders,
    create_market_order,
    fetch_ohlcv,
    set_leverage,
    verify_credentials,
)
from strategy import generate_trade_signal, ohlcv_to_dataframe

bot_state: Dict[str, Any] = {
    "running": False,
    "status": "Stopped",
    "logs": [],
}
bot_lock = threading.Lock()


def append_log(message: str) -> None:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with bot_lock:
        bot_state["logs"].append(f"[{timestamp}] {message}")
        bot_state["status"] = message


def _initialize_streamlit_state() -> None:
    defaults = {
        "exchange": "Binance",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "leverage": 5,
        "trade_amount": 50.0,
        "api_key": "",
        "secret_key": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def run_bot(config: Dict[str, Any]) -> None:
    if bot_state["running"]:
        append_log("Bot is already running.")
        return

    bot_state["running"] = True
    bot_state["status"] = "Starting bot..."
    bot_state["logs"] = []

    def worker() -> None:
        try:
            asyncio.run(_trading_loop(config))
        except Exception as err:
            append_log(f"Bot stopped with error: {err}")
        finally:
            bot_state["running"] = False
            append_log("Bot stopped.")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


def stop_bot() -> None:
    if bot_state["running"]:
        bot_state["running"] = False
        append_log("Stop requested. Waiting for current cycle to finish...")
    else:
        append_log("Bot is not running.")


async def _trading_loop(config: Dict[str, Any]) -> None:
    exchange = None
    try:
        exchange = build_exchange(config["exchange"], config["api_key"], config["secret_key"])
        append_log(f"Connecting to {config['exchange']} as {config['symbol']}.")

        await asyncio.to_thread(exchange.load_markets)
        await verify_credentials(exchange)
        append_log("API credentials verified.")

        try:
            await set_leverage(exchange, config["symbol"], config["leverage"])
            append_log(f"Leverage set to {config['leverage']}x.")
        except Exception as err:
            append_log(f"Leverage warning: {err}")

        while bot_state["running"]:
            try:
                ohlcv = await fetch_ohlcv(exchange, config["symbol"], config["timeframe"], limit=80)
                df = ohlcv_to_dataframe(ohlcv)
                signal = generate_trade_signal(df)

                if signal is None:
                    append_log("No valid trade signal found.")
                else:
                    append_log(
                        f"Signal: {signal['type']} {signal['direction'].upper()} at {signal['entry']:.2f} | "
                        f"SL={signal['stop_loss']:.2f} TP={signal['take_profit']:.2f}"
                    )
                    quantity = await calculate_position_quantity(
                        exchange,
                        config["symbol"],
                        config["trade_amount"],
                        config["leverage"],
                    )
                    order = await create_market_order(
                        exchange,
                        config["symbol"],
                        "buy" if signal["direction"] == "buy" else "sell",
                        quantity,
                    )
                    append_log(f"Market order executed: {order.get('id', 'unknown')}.")
                    try:
                        bracket = await create_bracket_orders(
                            exchange,
                            config["symbol"],
                            "buy" if signal["direction"] == "buy" else "sell",
                            quantity,
                            signal["stop_loss"],
                            signal["take_profit"],
                        )
                        append_log(f"Bracket orders created: {list(bracket.keys())}.")
                    except NotImplementedError as err:
                        append_log(f"Bracket order skipped: {err}")
                    except Exception as err:
                        append_log(f"Bracket order error: {err}")

                await asyncio.sleep(30)
            except Exception as err:
                append_log(f"Cycle error: {err}")
                await asyncio.sleep(10)
    except Exception as err:
        append_log(f"Initialization error: {err}")
    finally:
        if exchange is not None:
            try:
                await asyncio.to_thread(exchange.close)
            except Exception:
                pass


def build_interface() -> None:
    st.set_page_config(page_title="Crypto Futures Trading Bot", layout="wide")
    _initialize_streamlit_state()

    st.title("Crypto Futures Trading Bot")
    st.markdown(
        "This bot detects support/resistance, breakout moves, and range reversal patterns on Binance Futures or Weex Futures."
    )

    with st.sidebar:
        st.header("Bot Configuration")
        exchange = st.selectbox("Exchange", ["Binance", "Weex"], index=0)
        st.session_state.exchange = exchange
        st.session_state.api_key = st.text_input("API Key", type="password", value=st.session_state.api_key)
        st.session_state.secret_key = st.text_input("Secret Key", type="password", value=st.session_state.secret_key)
        st.session_state.symbol = st.text_input("Trading Pair", value=st.session_state.symbol)
        st.session_state.timeframe = st.selectbox("Timeframe", ["15m", "1h"], index=0)
        st.session_state.leverage = st.number_input(
            "Leverage (x)", min_value=1, max_value=125, value=st.session_state.leverage, step=1
        )
        st.session_state.trade_amount = st.number_input(
            "Trade Amount (USDT)", min_value=1.0, value=st.session_state.trade_amount, step=5.0
        )
        st.write("---")
        start_button = st.button("Start Bot")
        stop_button = st.button("Stop Bot")
        st.write("---")
        st.write("Bot status:")
        st.success(bot_state["status"] if bot_state["running"] else "Stopped")
        st.write(f"Running: {bot_state['running']}")
        st.write(f"Last update: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

        if start_button:
            if not st.session_state.api_key or not st.session_state.secret_key:
                append_log("API Key and Secret Key are required before starting.")
            elif bot_state["running"]:
                append_log("Bot is already running.")
            else:
                config = {
                    "exchange": st.session_state.exchange,
                    "api_key": st.session_state.api_key,
                    "secret_key": st.session_state.secret_key,
                    "symbol": st.session_state.symbol,
                    "timeframe": st.session_state.timeframe,
                    "leverage": st.session_state.leverage,
                    "trade_amount": st.session_state.trade_amount,
                }
                run_bot(config)

        if stop_button:
            stop_bot()

    st.subheader("Trade Log")
    log_lines = bot_state["logs"][-50:]
    for line in log_lines:
        st.text(line)

    if not bot_state["running"]:
        st.info("Press Start Bot to begin monitoring and trading.")
