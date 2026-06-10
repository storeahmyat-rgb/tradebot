import asyncio
from typing import Any, Dict, Optional

import ccxt

EXCHANGE_MAP = {
    "Binance": "binanceusdm",
    "Weex": "weex",
}


def build_exchange(exchange_name: str, api_key: str, secret: str) -> ccxt.Exchange:
    """Build a CCXT exchange instance for futures trading."""
    exchange_id = EXCHANGE_MAP.get(exchange_name)
    if not exchange_id:
        raise ValueError(f"Unsupported exchange '{exchange_name}'. Supported exchanges: {', '.join(EXCHANGE_MAP)}")

    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise ImportError(
            f"ccxt does not support exchange ID '{exchange_id}'. Please upgrade ccxt or choose another exchange."
        )

    config: Dict[str, Any] = {
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
        },
    }

    exchange = exchange_class(config)
    if exchange_id == "binanceusdm":
        exchange.options["defaultType"] = "future"
        exchange.options["recvWindow"] = 10000
    return exchange


async def verify_credentials(exchange: ccxt.Exchange) -> Dict[str, Any]:
    """Verify the API credentials by fetching the account balance."""
    return await asyncio.to_thread(exchange.fetch_balance)


async def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 80) -> Any:
    return await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe, None, limit)


async def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> Dict[str, Any]:
    return await asyncio.to_thread(exchange.fetch_ticker, symbol)


async def fetch_balance(exchange: ccxt.Exchange) -> Dict[str, Any]:
    return await asyncio.to_thread(exchange.fetch_balance)


async def set_leverage(exchange: ccxt.Exchange, symbol: str, leverage: int) -> Dict[str, Any]:
    """Set leverage for a futures symbol if the exchange offers the method."""
    if hasattr(exchange, "set_leverage"):
        return await asyncio.to_thread(exchange.set_leverage, leverage, symbol)

    raise NotImplementedError("This exchange does not support setting leverage through CCXT.")


async def calculate_position_quantity(
    exchange: ccxt.Exchange,
    symbol: str,
    amount_usdt: float,
    leverage: int,
) -> float:
    """Calculate the position quantity for a given USDT amount and leverage."""
    ticker = await fetch_ticker(exchange, symbol)
    price = ticker.get("last") or ticker.get("close")
    if price is None or price <= 0:
        raise ValueError("Unable to determine price to calculate order size.")

    notional_usdt = amount_usdt * leverage
    raw_quantity = notional_usdt / price
    if not hasattr(exchange, "amount_to_precision"):
        return float(raw_quantity)

    exchange.load_markets()
    precision_quantity = exchange.amount_to_precision(symbol, raw_quantity)
    return float(precision_quantity)


async def create_market_order(
    exchange: ccxt.Exchange,
    symbol: str,
    side: str,
    amount: float,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params or {}
    return await asyncio.to_thread(exchange.create_order, symbol, "market", side, amount, None, params)


async def create_bracket_orders(
    exchange: ccxt.Exchange,
    symbol: str,
    side: str,
    amount: float,
    stop_loss_price: float,
    take_profit_price: float,
) -> Dict[str, Any]:
    """Create optional stop-loss and take-profit orders for exchanges that support bracket-style orders."""
    results: Dict[str, Any] = {}
    close_side = "sell" if side == "buy" else "buy"
    params = {"closePosition": True, "reduceOnly": True}

    if exchange.id == "binanceusdm":
        results["take_profit"] = await asyncio.to_thread(
            exchange.create_order,
            symbol,
            "TAKE_PROFIT_MARKET",
            close_side,
            amount,
            None,
            {**params, "stopPrice": take_profit_price},
        )
        results["stop_loss"] = await asyncio.to_thread(
            exchange.create_order,
            symbol,
            "STOP_MARKET",
            close_side,
            amount,
            None,
            {**params, "stopPrice": stop_loss_price},
        )
        return results

    raise NotImplementedError("Bracket orders are not implemented for this exchange through CCXT.")
