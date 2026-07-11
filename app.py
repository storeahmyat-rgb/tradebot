"""
Flask Web Application - Binance Futures Trading Bot UI

Endpoints:
- GET  /              -> Dashboard
- GET  /api/config    -> Get current config
- POST /api/config    -> Save config
- POST /api/start     -> Start bot
- POST /api/stop      -> Stop bot
- GET  /api/status    -> Get bot status
- POST /api/close     -> Close position for one symbol (POST body: {"symbol":"BTCUSDT"})
- GET  /api/price     -> Get mark price for a symbol
- GET  /api/balance   -> Get USDT balance

SocketIO events emitted:
- log, status, indicators, position, signal, chart_data
"""
from __future__ import annotations

# ============================================================
# EVENTLET MONKEY PATCH — MUST BE FIRST!
# Railway (and most PaaS) need a real async WebSocket server.
# Flask-SocketIO's "threading" mode falls back to long-polling
# and times out behind Railway's reverse proxy. Eventlet gives
# us proper ws:// + wss:// support and keeps threading.Thread
# working (eventlet provides a greenlet-based Thread shim).
# ============================================================
try:
    import eventlet
    eventlet.monkey_patch(thread=True, select=True, socket=True, time=True)
    _ASYNC_MODE = "eventlet"
except ImportError:  # eventlet not installed -> fall back to threading
    _ASYNC_MODE = "threading"

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response, session
from flask_socketio import SocketIO

from bot.engine import BotEngine
from bot.license import LicenseManager, get_hardware_id
from bot.secret import get_admin_password
from bot.antitamper import check_integrity, is_debugger_present

# ---------- Setup ----------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LICENSE_FILE = BASE_DIR / "licenses.dat"  # Now encrypted, .dat extension
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Setup logging FIRST (before anti-tamper check uses logger)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("app")

# Admin password - loaded from obfuscated secret module
ADMIN_PASSWORD = get_admin_password()

# ---------- Anti-tamper check at startup ----------
_tamper_result = check_integrity()
if not _tamper_result["ok"]:
    logger.error("⚠️  TAMPER DETECTED: %s", "; ".join(_tamper_result["warnings"]))
if is_debugger_present():
    logger.error("⚠️  DEBUGGER DETECTED - license protection may be bypassed")

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
app.config["SECRET_KEY"] = "binance-futures-bot-license-protected-2024"

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=_ASYNC_MODE,           # "eventlet" on Railway, "threading" as fallback
    ping_timeout=120,                 # generous — Railway proxy can be slow
    ping_interval=25,
    max_http_buffer_size=10_000_000,  # 10 MB — large chart_data frames
    logger=False,
    engineio_logger=False,
)

# ---------- License Manager ----------
license_mgr = LicenseManager(str(LICENSE_FILE), admin_secret=ADMIN_PASSWORD)

# ---------- Default config ----------

DEFAULT_CONFIG = {
    "api_key": "",
    "api_secret": "",
    "api_passphrase": "",        # WEEX only (ignored by Binance)
    "exchange": "binance",       # 'binance' or 'weex'
    "testnet": True,
    "symbol": "BTCUSDT",
    "symbols_list": ["BTCUSDT"],
    "timeframe": "1d",
    "leverage": 10,
    "amount_mode": "fixed",   # 'fixed' or 'percent'
    "amount": 100,
    "amount_pct": 10,
    "stop_loss_pct": 0,        # 0 = OFF. e.g. 5 = 5% SL
    "take_profit_pct": 0,      # 0 = OFF (use opposite-signal exit). e.g. 10 = 10% TP
    "mode": "both",
    "auto_start": False,
    # ---- Notification settings ----
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "email_enabled": False,
    "email_smtp_server": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_sender": "",
    "email_password": "",
    "email_receiver": "",
    "whatsapp_enabled": False,
    "whatsapp_phone": "",
    "whatsapp_apikey": "",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            merged = {**DEFAULT_CONFIG, **cfg}
            return merged
        except Exception as e:
            logger.error("Failed to load config: %s", e)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()
ENGINE = BotEngine(socketio, CONFIG)


# ---------- License helper ----------

def require_license():
    """Check if user has valid license. Returns (ok, error_response)."""
    result = license_mgr.validate()
    if result.get("success"):
        return True, None
    return False, jsonify({"success": False, "license_required": True,
                           "error": result.get("error", "License required")})


# ---------- License routes ----------

@app.route("/login")
def login_page():
    """Login page - user enters license key here."""
    return render_template("login.html")


@app.route("/api/license/status", methods=["GET"])
def license_status():
    """Check current license status."""
    result = license_mgr.validate()
    hw_id = get_hardware_id()
    return jsonify({
        "success": result.get("success", False),
        "license": result.get("license"),
        "error": result.get("error"),
        "hw_id": hw_id,
    })


@app.route("/api/license/activate", methods=["POST"])
def license_activate():
    """Activate a license key on this PC."""
    data = request.get_json(force=True)
    key = data.get("key", "").strip()
    if not key:
        return jsonify({"success": False, "error": "License key daalo"})
    result = license_mgr.activate(key)
    return jsonify(result)


@app.route("/api/license/deactivate", methods=["POST"])
def license_deactivate():
    """Deactivate license on this PC (user wants to switch)."""
    return jsonify(license_mgr.deactivate())


# ---------- Admin routes ----------

@app.route("/admin")
def admin_page():
    """Admin panel - manage license keys."""
    return render_template("admin.html")


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    """Admin login - verify password."""
    data = request.get_json(force=True)
    password = data.get("password", "")
    if password == ADMIN_PASSWORD:
        session["admin"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Admin password galat hai"})


@app.route("/api/admin/keys", methods=["GET"])
def admin_list_keys():
    """List all license keys (admin only)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Admin login zaroori hai"})
    return jsonify(license_mgr.admin_list_keys(ADMIN_PASSWORD))


@app.route("/api/admin/keys/create", methods=["POST"])
def admin_create_key():
    """Create new license key (admin only)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Admin login zaroori hai"})
    data = request.get_json(force=True)
    plan_days = int(data.get("plan_days", 30))
    note = data.get("note", "")
    return jsonify(license_mgr.admin_create_key(ADMIN_PASSWORD, plan_days, note))


@app.route("/api/admin/keys/revoke", methods=["POST"])
def admin_revoke_key():
    """Revoke a license key (admin only)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Admin login zaroori hai"})
    data = request.get_json(force=True)
    key = data.get("key", "")
    return jsonify(license_mgr.admin_revoke_key(ADMIN_PASSWORD, key))


@app.route("/api/admin/keys/delete", methods=["POST"])
def admin_delete_key():
    """Permanently delete a license key (admin only)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Admin login zaroori hai"})
    data = request.get_json(force=True)
    key = data.get("key", "")
    return jsonify(license_mgr.admin_delete_key(ADMIN_PASSWORD, key))


@app.route("/api/admin/keys/extend", methods=["POST"])
def admin_extend_key():
    """Extend a license by extra days (admin only)."""
    if not session.get("admin"):
        return jsonify({"success": False, "error": "Admin login zaroori hai"})
    data = request.get_json(force=True)
    key = data.get("key", "")
    extra_days = int(data.get("extra_days", 0))
    if extra_days <= 0:
        return jsonify({"success": False, "error": "extra_days > 0 hona chahiye"})
    return jsonify(license_mgr.admin_extend_key(ADMIN_PASSWORD, key, extra_days))


# ---------- Routes ----------

@app.route("/")
def index():
    """Main dashboard - requires license."""
    ok, err = require_license()
    if not ok:
        return render_template("login.html", license_required=True)
    return render_template("dashboard.html", config=CONFIG)


@app.route("/favicon.ico")
def favicon():
    """1x1 transparent PNG to avoid 404 noise."""
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c63000100000005000100"
        "0d0a2db40000000049454e44ae426082"
    )
    return Response(png_bytes, mimetype="image/png")


@app.route("/api/config", methods=["GET"])
def get_config():
    ok, err = require_license()
    if not ok:
        return err
    safe = {**CONFIG}
    if safe.get("api_key"):
        safe["api_key_masked"] = safe["api_key"][:4] + "***" + safe["api_key"][-4:]
    if safe.get("api_secret"):
        safe["api_secret_masked"] = "***"
    safe["api_key"] = safe.get("api_key", "")
    safe["api_secret"] = safe.get("api_secret", "")
    return jsonify(safe)


@app.route("/api/config", methods=["POST"])
def update_config():
    ok, err = require_license()
    if not ok:
        return err
    global CONFIG
    data = request.get_json(force=True)

    for k in ["exchange", "api_passphrase", "symbol", "symbols_list", "timeframe",
              "leverage", "amount", "amount_mode", "amount_pct",
              "stop_loss_pct", "take_profit_pct", "mode", "testnet", "auto_start",
              # Notification fields
              "telegram_enabled", "telegram_bot_token", "telegram_chat_id",
              "email_enabled", "email_smtp_server", "email_smtp_port",
              "email_sender", "email_password", "email_receiver",
              "whatsapp_enabled", "whatsapp_phone", "whatsapp_apikey"]:
        if k in data:
            CONFIG[k] = data[k]

    if data.get("api_key"):
        CONFIG["api_key"] = data["api_key"]
    if data.get("api_secret"):
        CONFIG["api_secret"] = data["api_secret"]

    # Type coercion
    CONFIG["exchange"] = (CONFIG.get("exchange") or "binance").lower()
    if CONFIG["exchange"] not in ("binance", "weex"):
        CONFIG["exchange"] = "binance"
    # Max leverage: Binance = 125x, WEEX = 500x
    max_lev = 500 if CONFIG["exchange"] == "weex" else 125
    CONFIG["leverage"] = max(1, min(max_lev, int(CONFIG["leverage"])))
    CONFIG["amount"] = max(1, float(CONFIG["amount"]))
    CONFIG["amount_pct"] = max(1, min(100, float(CONFIG["amount_pct"])))
    # Strict SL: minimum 0.5%, maximum 50%. Cannot be 0 (disabled).
    CONFIG["stop_loss_pct"] = max(0.5, min(50, float(CONFIG.get("stop_loss_pct", 2))))
    # TP is always SL × 3 (hardcoded 1:3 RR) - ignore user input
    CONFIG["take_profit_pct"] = CONFIG["stop_loss_pct"] * 3
    CONFIG["testnet"] = bool(CONFIG["testnet"])
    # Notification booleans
    CONFIG["telegram_enabled"] = bool(CONFIG.get("telegram_enabled"))
    CONFIG["email_enabled"] = bool(CONFIG.get("email_enabled"))
    CONFIG["whatsapp_enabled"] = bool(CONFIG.get("whatsapp_enabled"))
    CONFIG["email_smtp_port"] = int(CONFIG.get("email_smtp_port", 587))

    # Ensure symbols_list is always a list of uppercase strings
    if isinstance(CONFIG.get("symbols_list"), str):
        CONFIG["symbols_list"] = [s.strip().upper()
                                  for s in CONFIG["symbols_list"].split(",")
                                  if s.strip()]
    elif isinstance(CONFIG.get("symbols_list"), list):
        CONFIG["symbols_list"] = [str(s).strip().upper()
                                  for s in CONFIG["symbols_list"] if str(s).strip()]

    # Keep `symbol` (primary, first) in sync
    if CONFIG["symbols_list"]:
        CONFIG["symbol"] = CONFIG["symbols_list"][0]
    elif isinstance(CONFIG.get("symbol"), str):
        CONFIG["symbols_list"] = [CONFIG["symbol"].strip().upper()]

    save_config(CONFIG)
    logger.info("Config updated: exchange=%s symbols=%s tf=%s lev=%sx amount=%s mode=%s testnet=%s",
                CONFIG["exchange"], CONFIG["symbols_list"], CONFIG["timeframe"], CONFIG["leverage"],
                (f"{CONFIG['amount_pct']}% wallet" if CONFIG['amount_mode'] == 'percent'
                 else f"${CONFIG['amount']}"),
                CONFIG["mode"], CONFIG["testnet"])
    # Hide sensitive fields in response
    safe_keys = ("api_secret", "email_password", "telegram_bot_token", "whatsapp_apikey")
    return jsonify({
        "success": True,
        "config": {k: v for k, v in CONFIG.items() if k not in safe_keys}
    })


@app.route("/api/test_notification", methods=["POST"])
def test_notification():
    """Send a test notification. Accepts config directly in body so user
    can test without saving first."""
    try:
        data = request.get_json(silent=True) or {}
        # Build a temp config by merging current CONFIG with provided values
        temp_config = dict(CONFIG)
        # Update from request body
        for k in ["telegram_enabled", "telegram_bot_token", "telegram_chat_id",
                  "email_enabled", "email_smtp_server", "email_smtp_port",
                  "email_sender", "email_password", "email_receiver",
                  "whatsapp_enabled", "whatsapp_phone", "whatsapp_apikey"]:
            if k in data and data[k] is not None:
                temp_config[k] = data[k]

        # Use a fresh Notifier instance (don't touch the running engine's)
        from bot.notifier import Notifier
        n = Notifier(temp_config)
        results = n.send(
            "Test Notification",
            "Yeh bot se test notification hai. Agar yeh aapko mil raha hai, toh settings sahi hain!"
        )
        # Build a readable summary
        summary_parts = []
        any_enabled = False
        any_success = False
        for channel in ("telegram", "email", "whatsapp"):
            enabled_flag = temp_config.get(f"{channel}_enabled")
            if enabled_flag:
                any_enabled = True
                r = results.get(channel)
                if r is None:
                    summary_parts.append(f"{channel}: still sending...")
                elif r.get("success"):
                    summary_parts.append(f"{channel}: ✅ sent")
                    any_success = True
                else:
                    summary_parts.append(f"{channel}: ❌ {r.get('error', 'unknown')}")

        if not any_enabled:
            return jsonify({
                "success": False,
                "error": "Koi notification channel enabled nahi hai. Pehle checkbox on karein.",
                "results": results
            })

        return jsonify({
            "success": any_success,
            "message": " | ".join(summary_parts),
            "results": results
        })
    except Exception as e:
        logger.exception("Test notification error")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/start", methods=["POST"])
def start_bot():
    try:
        ok, err = require_license()
        if not ok:
            return err
        if not CONFIG.get("api_key") or not CONFIG.get("api_secret"):
            return jsonify({"success": False, "error": "API key aur secret pehle set karo"})
        if CONFIG.get("exchange") == "weex" and not CONFIG.get("api_passphrase"):
            return jsonify({"success": False, "error": "WEEX ke liye Passphrase bhi chahiye"})
        if not CONFIG.get("symbols_list"):
            return jsonify({"success": False, "error": "Kam az kam ek symbol add karo"})
        logger.info("Starting bot with config: exchange=%s symbols=%s", CONFIG.get("exchange"), CONFIG.get("symbols_list"))
        result = ENGINE.start(CONFIG)
        logger.info("Bot start result: %s", result)
        return jsonify(result)
    except Exception as e:
        logger.exception("START BOT CRASHED:")
        return jsonify({"success": False, "error": f"Bot start error: {str(e)}"})


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    result = ENGINE.stop()
    return jsonify(result)


@app.route("/api/force_stop", methods=["POST"])
def force_stop_bot():
    """Force stop - always works, even if bot is in zombie state."""
    result = ENGINE.force_stop()
    return jsonify(result)


@app.route("/api/active_symbol", methods=["POST"])
def set_active_symbol():
    """Tell the backend which coin the UI is currently viewing.
    Only this coin's chart data will be sent via WebSocket."""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"success": False, "error": "Symbol required"})
    ENGINE.set_active_symbol(symbol)
    return jsonify({"success": True, "active_symbol": symbol})


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify(ENGINE.status())


@app.route("/api/close", methods=["POST"])
def close_position():
    if not ENGINE.trader:
        return jsonify({"success": False, "error": "Bot not connected"})
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol") or CONFIG.get("symbol", "BTCUSDT")
    result = ENGINE.trader.close_position(symbol)
    socketio.emit("log", {"level": "info",
                          "msg": f"Manual close requested for {symbol}: {result}"})
    return jsonify(result)


@app.route("/api/price", methods=["GET"])
def get_price():
    symbol = request.args.get("symbol", CONFIG.get("symbol", "BTCUSDT"))
    if not ENGINE.trader:
        return jsonify({"success": False, "error": "Bot not connected"})
    try:
        price = ENGINE.trader.get_mark_price(symbol)
        return jsonify({"success": True, "symbol": symbol, "price": price})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/symbols", methods=["GET"])
def get_all_symbols():
    """Get all available USDT perpetual symbols from the exchange.
    Works even when bot is not running (creates temporary connection)."""
    # If bot is running, use existing trader
    if ENGINE.trader:
        try:
            symbols = ENGINE.trader.get_all_symbols()
            return jsonify({"success": True, "symbols": symbols, "count": len(symbols)})
        except Exception as e:
            logger.error(f"Failed to fetch symbols: {e}")

    # Bot not running - create temporary trader to fetch symbols
    try:
        from bot.engine import get_trader
        exchange = CONFIG.get("exchange", "binance")
        if exchange == "weex":
            if not CONFIG.get("api_key") or not CONFIG.get("api_secret") or not CONFIG.get("api_passphrase"):
                return jsonify({"success": False, "error": "WEEX API credentials not set",
                                "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]})
        else:
            if not CONFIG.get("api_key") or not CONFIG.get("api_secret"):
                return jsonify({"success": False, "error": "Binance API credentials not set",
                                "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]})

        temp_trader = get_trader(CONFIG)
        symbols = temp_trader.get_all_symbols()
        return jsonify({"success": True, "symbols": symbols, "count": len(symbols)})
    except Exception as e:
        logger.error(f"Failed to fetch symbols (temp trader): {e}")
        return jsonify({"success": False, "error": str(e),
                        "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
                                    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
                                    "LINKUSDT", "MATICUSDT"]})


@app.route("/api/test_chart", methods=["GET"])
def test_chart():
    """Test if klines can be fetched. Returns sample data for debugging."""
    symbol = request.args.get("symbol", CONFIG.get("symbol", "BTCUSDT"))
    if not ENGINE.trader:
        return jsonify({"success": False, "error": "Bot not running"})
    try:
        df = ENGINE.trader.get_klines(symbol, interval=CONFIG.get("timeframe", "5m"), limit=5)
        if df is None or len(df) == 0:
            return jsonify({"success": False, "error": "No klines data returned", "rows": 0})
        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "time": int(ts.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
        return jsonify({
            "success": True,
            "symbol": symbol,
            "rows": len(df),
            "candles": candles,
            "columns": list(df.columns),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/balance", methods=["GET"])
def get_balance():
    if not ENGINE.trader:
        return jsonify({"success": False, "error": "Bot not connected"})
    try:
        bal = ENGINE.trader.get_balance()
        return jsonify({"success": True, "balance": bal})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ---------- SocketIO ----------

@socketio.on("connect")
def on_connect():
    socketio.emit("log", {"level": "info", "msg": "UI connected to bot"})
    socketio.emit("status", ENGINE.status())


@socketio.on("disconnect")
def on_disconnect():
    logger.info("UI disconnected")


# ---------- Entry point ----------

def _crash_handler(exc_type, exc_value, exc_tb):
    """Custom exception handler - logs error and keeps console open."""
    import traceback
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.error("FATAL ERROR - Bot crashed:\n%s", error_msg)
    # Save to crash log file
    try:
        with open("crash.log", "w", encoding="utf-8") as f:
            f.write(f"Binance Futures Bot - Crash Report\n")
            f.write(f"Time: {datetime.now().isoformat()}\n")
            f.write(f"Python: {sys.version}\n")
            f.write(f"Frozen: {getattr(sys, 'frozen', False)}\n")
            if getattr(sys, 'frozen', False):
                f.write(f"Executable: {sys.executable}\n")
            f.write(f"\n{'='*60}\n\n")
            f.write(error_msg)
    except Exception:
        pass
    # Print to console
    print("\n" + "=" * 60)
    print(" ⚠️  FATAL ERROR - Bot crashed!")
    print("=" * 60)
    print(error_msg)
    print("=" * 60)
    print("Crash log saved to: crash.log")
    print("Please share this with the developer.")
    print("=" * 60)
    # Keep console open so user can read the error — but ONLY when running
    # interactively (local dev). On Railway / Docker stdin is closed, so
    # calling input() raises EOFError and crashes the container into a
    # crash-loop. Detect TTY and skip the prompt there.
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except Exception:
            import time
            time.sleep(30)
    else:
        logger.error("Non-interactive environment detected — exiting without prompt.")
    sys.exit(1)


if __name__ == "__main__":
    # Set custom exception handler
    import sys as _sys
    _sys.excepthook = _crash_handler

    try:
        # Railway injects PORT env var. Default to 5000 for local dev.
        port = int(os.environ.get("PORT", 5000))
        host = os.environ.get("HOST", "0.0.0.0")
        logger.info("=" * 60)
        logger.info(" Binance Futures Bot - EMA Quad Strategy (Multi-Symbol)")
        logger.info(" Async mode: %s", _ASYNC_MODE)
        logger.info(" Listening on http://%s:%d", host, port)
        logger.info(" Press Ctrl+C to stop")
        logger.info("=" * 60)
        # NOTE: do NOT pass log_output / log when running under eventlet —
        # Werkzeug's logging hooks deadlock with greenlets in some setups.
        socketio.run(
            app,
            host=host,
            port=port,
            debug=False,
            allow_unsafe_werkzeug=True,
            use_reloader=False,
        )
    except Exception as e:
        # Trigger our custom handler
        _crash_handler(type(e), e, e.__traceback__)


# ============================================================
# WSGI ENTRYPOINT (for Railway / gunicorn-style runners)
# Some Railway setups prefer `gunicorn app:app` over `python app.py`.
# Exposing `app` at module level lets Railway fall back to WSGI mode
# if SocketIO's async server is not desired. SocketIO still works
# because flask_socketio decorates the same app object.
# ============================================================
# (app + socketio are already defined above)
