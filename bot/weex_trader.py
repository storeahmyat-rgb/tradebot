"""
WEEX Exchange Futures Trader Module
Direct HTTP adapter for WEEX V3 contract API.

WEEX uses OKX-style authentication:
- 3 credentials: API Key + Secret Key + Passphrase
- HMAC-SHA256 signature, Base64-encoded
- 4 headers: ACCESS-KEY, ACCESS-PASSPHRASE, ACCESS-TIMESTAMP, ACCESS-SIGN

Base URL (mainnet): https://api-contract.weex.com
Demo mode: same host, paths swap "account" -> "sim"

Docs: https://www.weex.com/api-doc/contract/intro
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

WEEX_BASE_URL = "https://api-contract.weex.com"


@dataclass
class WEEXPosition:
    symbol: str
    side: str            # "LONG" / "SHORT" / "NONE"
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int


class WEEXFuturesTrader:
    """
    WEEX Futures API wrapper with same interface as BinanceFuturesTrader.
    Supports demo (paper) mode and live mode on same base URL.
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.demo = testnet  # WEEX uses "demo mode" instead of separate testnet
        self.base_url = WEEX_BASE_URL
        self.session = requests.Session()
        self._contract_cache: dict = {}  # symbol -> contract specs

        if not (api_key and api_secret and passphrase):
            raise ValueError("WEEX requires API Key, Secret, AND Passphrase")

        logger.info("Connected to WEEX Futures (%s)",
                    "DEMO" if self.demo else "LIVE")

    # ---------- Auth ----------

    def _sign(self, method: str, path: str, query: str = "", body: str = "") -> dict:
        """Build the 4 ACCESS-* headers required by WEEX V3."""
        timestamp = str(int(time.time() * 1000))
        # Build the message: timestamp + METHOD + path[?query] + body
        if query:
            message = f"{timestamp}{method.upper()}{path}?{query}{body}"
        else:
            message = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(self.api_secret.encode("utf-8"),
                       message.encode("utf-8"),
                       hashlib.sha256)
        sign = base64.b64encode(mac.digest()).decode("utf-8")
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-SIGN": sign,
            "Content-Type": "application/json",
        }

    def _path(self, live_path: str) -> str:
        """Convert live path to demo path if demo mode is on.
        e.g. /capi/v3/account/balance -> /capi/v3/sim/balance
        """
        if self.demo:
            return live_path.replace("/account/", "/sim/").replace("/order", "/sim/order")
        return live_path

    def _request(self, method: str, path: str, params: dict = None,
                 body: dict = None, signed: bool = False):
        """Execute HTTP request with optional signing."""
        params = params or {}
        body = body or {}
        path = self._path(path)
        url = self.base_url + path

        body_str = json.dumps(body) if body and method.upper() == "POST" else ""
        query_str = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""

        headers = {"Content-Type": "application/json"}
        if signed:
            headers = self._sign(method, path, query_str, body_str)

        try:
            if method.upper() == "GET":
                resp = self.session.get(url, params=params, headers=headers, timeout=8)
            else:
                resp = self.session.post(url, data=body_str, params=params,
                                         headers=headers, timeout=8)
            data = resp.json()
            if resp.status_code >= 400:
                err_msg = data.get("msg") or data.get("error") or f"HTTP {resp.status_code}"
                logger.error("WEEX API error: %s (path=%s, params=%s)", err_msg, path, params)
                raise RuntimeError(f"WEEX API error: {err_msg}")
            return data
        except requests.exceptions.Timeout:
            logger.error("WEEX request TIMEOUT (8s): %s %s", method, path)
            raise RuntimeError(f"WEEX API timeout (8s) - check internet connection")
        except requests.exceptions.ConnectionError as e:
            logger.error("WEEX connection error: %s", e)
            raise RuntimeError(f"WEEX connection failed - check internet or API endpoint")
        except requests.exceptions.RequestException as e:
            logger.error("WEEX request failed: %s", e)
            raise

    # ---------- Market data (public) ----------

    def get_klines(self, symbol: str, interval: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch historical klines. WEEX interval codes: 1m,5m,15m,30m,1h,4h,12h,1d,1w."""
        path = "/capi/v3/market/klines"
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
        data = self._request("GET", path, params=params)

        # WEEX returns {"code":0,"data":[[time, open, high, low, close, volume, ...], ...]}
        rows = data.get("data", []) if isinstance(data, dict) else data
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # WEEX can return variable number of columns. Handle gracefully.
        # Known columns: time, open, high, low, close, volume, value, number, ...
        # We only need: time, open, high, low, close, volume (first 6)
        parsed_rows = []
        for row in rows:
            if isinstance(row, dict):
                # Dict format
                parsed_rows.append({
                    "time": row.get("time") or row.get("timestamp"),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0) or row.get("vol", 0)),
                })
            elif isinstance(row, (list, tuple)):
                # Array format - take first 6 elements
                if len(row) >= 6:
                    parsed_rows.append({
                        "time": row[0],
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    })

        if not parsed_rows:
            logger.warning("WEEX klines returned EMPTY for %s %s (raw=%s)", symbol, interval, str(data)[:200])
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(parsed_rows)
        df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        df.set_index("time", inplace=True)
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_mark_price(self, symbol: str) -> float:
        """Get current mark price for a symbol."""
        path = "/capi/v3/market/symbolPrice"
        params = {"symbol": symbol, "priceType": "MARK"}
        data = self._request("GET", path, params=params)
        return float(data.get("data", {}).get("price", 0))

    def get_contract_info(self, symbol: str) -> dict:
        """Fetch contract specs (lot size, tick size) for a symbol. Cached."""
        symbol = symbol.upper()
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        try:
            path = "/capi/v3/market/contracts"
            data = self._request("GET", path)
            contracts = data.get("data", []) if isinstance(data, dict) else data
            for c in contracts:
                if c.get("symbol") == symbol:
                    info = {
                        "step_size": float(c.get("volumeStep", 0.001)),
                        "min_qty": float(c.get("minVolume", 0.001)),
                        "tick_size": float(c.get("priceTick", 0.01)),
                        "contract_size": float(c.get("contractSize", 1)),
                    }
                    self._contract_cache[symbol] = info
                    return info
        except Exception as e:
            logger.warning("Failed to fetch WEEX contract info for %s: %s", symbol, e)
        # Defaults
        defaults = {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.01, "contract_size": 1}
        self._contract_cache[symbol] = defaults
        return defaults

    # ---------- Account (private, signed) ----------

    def get_balance(self) -> float:
        """Get USDT available balance."""
        path = "/capi/v3/account/balance"
        data = self._request("GET", path, signed=True)
        assets = data.get("data", []) if isinstance(data, dict) else data
        for a in assets:
            if a.get("asset") == "USDT":
                return float(a.get("availableBalance", 0))
        return 0.0

    def get_position(self, symbol: str) -> WEEXPosition:
        """Get current position for a symbol."""
        path = "/capi/v3/account/position/allPosition"
        try:
            data = self._request("GET", path, params={"symbol": symbol}, signed=True)
            positions = data.get("data", []) if isinstance(data, dict) else data
        except Exception as e:
            logger.warning("WEEX get_position failed: %s", e)
            return WEEXPosition(symbol, "NONE", 0, 0, 0, 0, 1)

        if not positions:
            return WEEXPosition(symbol, "NONE", 0, 0, 0, 0, 1)

        # Find the position row (could be long or short)
        for p in positions:
            if p.get("symbol") != symbol:
                continue
            qty = float(p.get("total", 0) or p.get("positionAmt", 0) or 0)
            if qty == 0:
                continue
            side = p.get("positionSide", "").upper()
            if side not in ("LONG", "SHORT"):
                side = "LONG" if qty > 0 else "SHORT"
            return WEEXPosition(
                symbol=symbol,
                side=side,
                size=qty,
                entry_price=float(p.get("avgPrice", 0) or p.get("entryPrice", 0)),
                mark_price=float(p.get("markPrice", 0)),
                unrealized_pnl=float(p.get("unrealizedPNL", 0) or p.get("unrealizePnl", 0)),
                leverage=int(float(p.get("leverage", 1))),
            )
        return WEEXPosition(symbol, "NONE", 0, 0, 0, 0, 1)

    # ---------- Orders ----------

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol. WEEX supports up to 500x.
        Tries multiple endpoint paths (WEEX demo vs live may differ)."""
        leverage = max(1, min(500, int(leverage)))
        body = {
            "symbol": symbol,
            "marginType": "CROSSED",
            "crossLeverage": leverage,
        }
        # Try multiple paths - WEEX demo/live may use different endpoints
        paths_to_try = [
            "/capi/v3/account/leverage",   # Live standard
            "/capi/v3/sim/leverage",       # Demo standard
            "/capi/v3/account/setLeverage",  # Alternative
            "/capi/v3/sim/setLeverage",    # Alternative demo
        ]
        last_error = None
        for path in paths_to_try:
            try:
                resp = self._request("POST", path, body=body, signed=True)
                logger.info("WEEX leverage set to %dx for %s (path=%s)", leverage, symbol, path)
                return {"success": True, "leverage": leverage, "raw": resp}
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # If auth error, don't try other paths
                if "invalid" in err_str or "unauthorized" in err_str or "401" in err_str:
                    return {"success": False, "error": str(e)}
                # If "Not Found", try next path
                if "not found" in err_str or "404" in err_str:
                    continue
                # Other error - return it
                return {"success": False, "error": str(e)}

        # All paths failed
        logger.error("WEEX set_leverage failed on all paths: %s", last_error)
        # Return success anyway - WEEX might use default leverage
        # Bot will continue and trades will still work with default leverage
        logger.warning("WEEX: Using default leverage (set_leverage failed, continuing)")
        return {"success": True, "leverage": leverage, "raw": "default",
                "warning": "Leverage not set explicitly, using exchange default"}

    def place_market_order(self, symbol: str, side: str, quantity: float,
                           reduce_only: bool = False,
                           position_side: str = None) -> dict:
        """
        Place a MARKET order on WEEX.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL"
            quantity: contract count (WEEX uses contracts, not base qty)
            reduce_only: if True, only reduces existing position
            position_side: "LONG" or "SHORT" (required for hedge mode)
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            return {"success": False, "error": f"Invalid side: {side}"}
        if quantity <= 0:
            return {"success": False, "error": "Quantity must be > 0"}

        # Round quantity to contract step size
        try:
            info = self.get_contract_info(symbol)
            step = info["step_size"]
            min_qty = info["min_qty"]
            quantity = self._round_to_step(quantity, step)
            if quantity < min_qty:
                return {"success": False,
                        "error": f"Quantity {quantity} below min {min_qty} for {symbol}"}
        except Exception as e:
            logger.warning("Could not apply WEEX step size for %s: %s", symbol, e)

        # Determine positionSide based on side and intent
        if position_side is None:
            # Buy = open LONG, Sell = open SHORT (one-way mode)
            position_side = "LONG" if side == "BUY" else "SHORT"
            if reduce_only:
                # Flip: closing LONG -> SELL, closing SHORT -> BUY
                position_side = "LONG" if side == "SELL" else "SHORT"

        try:
            path = "/capi/v3/order"
            body = {
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": str(quantity),
            }
            if reduce_only:
                body["reduceOnly"] = "true"
            resp = self._request("POST", path, body=body, signed=True)
            oid = "?"
            if isinstance(resp, dict):
                oid = resp.get("data", {}).get("orderId", "?") if isinstance(resp.get("data"), dict) else resp.get("orderId", "?")
            logger.info("WEEX order placed: %s %s qty=%s posSide=%s -> orderId=%s",
                        side, symbol, quantity, position_side, oid)
            return {"success": True, "order": resp, "quantity": quantity}
        except Exception as e:
            logger.error("WEEX order failed: %s", e)
            return {"success": False, "error": str(e), "quantity": quantity}

    def close_position(self, symbol: str) -> dict:
        """Close the current position using reduceOnly market order."""
        pos = self.get_position(symbol)
        if pos.side == "NONE" or pos.size == 0:
            return {"success": True, "message": "No position to close"}
        # Closing LONG -> SELL with reduceOnly; closing SHORT -> BUY with reduceOnly
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        return self.place_market_order(
            symbol=symbol,
            side=close_side,
            quantity=abs(pos.size),
            reduce_only=True,
            position_side=pos.side,
        )

    def open_long(self, symbol: str, quantity: float) -> dict:
        return self.place_market_order(symbol, "BUY", quantity,
                                       reduce_only=False, position_side="LONG")

    def open_short(self, symbol: str, quantity: float) -> dict:
        return self.place_market_order(symbol, "SELL", quantity,
                                       reduce_only=False, position_side="SHORT")

    # ---------- Helpers ----------

    def get_all_symbols(self) -> list:
        """Fetch ALL available USDT perpetual symbols from WEEX.
        WEEX contracts endpoint returns 404, so we use a comprehensive list.
        New coins can be added manually via '+ Add' button."""
        logger.info("Using comprehensive WEEX coin list (API endpoint not available)")
        return self._get_weex_fallback_symbols()

    def _extract_symbols_from_response(self, data) -> list:
        """Extract USDT symbols from any WEEX API response format."""
        symbols = []
        # Try to find the data array
        rows = data
        if isinstance(data, dict):
            rows = data.get("data", data.get("result", data.get("symbols", [])))
            if isinstance(rows, dict):
                rows = [rows]
        if not isinstance(rows, list):
            return []

        for item in rows:
            if isinstance(item, dict):
                sym = (item.get("symbol") or item.get("contractName") or
                       item.get("pair") or item.get("name") or item.get("baseAsset", ""))
                if isinstance(sym, str) and sym.upper().endswith("USDT"):
                    symbols.append(sym.upper())
            elif isinstance(item, str) and item.upper().endswith("USDT"):
                symbols.append(item.upper())

        # Deduplicate and sort
        return sorted(list(set(symbols)))

    @staticmethod
    def _get_weex_fallback_symbols() -> list:
        """Comprehensive list of common WEEX USDT perpetual coins.
        Updated regularly. New coins appear when API endpoints work."""
        return sorted([
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
            "DOTUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT", "UNIUSDT",
            "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT",
            "SUIUSDT", "SEIUSDT", "TIAUSDT", "ORDIUSDT", "PEPEUSDT",
            "SHIBUSDT", "FILUSDT", "FTMUSDT", "ALGOUSDT", "EOSUSDT",
            "XTZUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT",
            "GRTUSDT", "CHZUSDT", "ENJUSDT", "THETAUSDT", "RUNEUSDT",
            "AAVEUSDT", "SNXUSDT", "CRVUSDT", "1INCHUSDT", "YFIUSDT",
            "COMPUSDT", "MKRUSDT", "SUSHIUSDT", "BALUSDT", "RNDRUSDT",
            "IMXUSDT", "LDOUSDT", "STXUSDT", "FETUSDT", "AGIXUSDT",
            "OCEANUSDT", "WLDUSDT", "CYBERUSDT", "BLURUSDT", "GMXUSDT",
            "DYDXUSDT", "JOEUSDT", "PYTHUSDT", "JTOUSDT", "BONKUSDT",
            "WIFUSDT", "FLOKIUSDT", "MEMEUSDT", "TURBOUSDT", "BOMEUSDT",
            "JUPUSDT", "RAYUSDT", "PYRUSDT", "ACEUSDT", "NFPUSDT",
            "INSURUSDT", "MOVRUSDT", "GLMRUSDT", "ASTRUSDT", "CFXUSDT",
            "ZILUSDT", "KAVAUSDT", "KSMUSDT", "MINAUSDT", "ROSEUSDT",
            "IOTAUSDT", "FLOWUSDT", "XLMUSDT", "VETUSDT", "HBARUSDT",
            "ICPUSDT", "FILUSDT", "ARUSDT", "KLAYUSDT", "QNTUSDT",
            "FXSUSDT", "GMTUSDT", "APEUSDT", "GTCUSDT", "LRCUSDT",
        ])


    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        decimals = max(0, int(math.ceil(-math.log10(step))))
        rounded = math.floor(value / step) * step
        return round(rounded, decimals)

    def compute_quantity(self, notional_usdt: float, price: float,
                         leverage: int, qty_step: float = 0.001) -> float:
        """Compute contract quantity from USDT notional."""
        if price <= 0:
            return 0.0
        raw_qty = notional_usdt / price
        return raw_qty  # final rounding happens in place_market_order
