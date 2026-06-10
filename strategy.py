import pandas as pd
import pandas_ta as ta
from typing import Any, Dict, List, Optional, Tuple


def ohlcv_to_dataframe(ohlcv: List[List[float]]) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("datetime", inplace=True)
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["rsi"].fillna(50, inplace=True)
    return df


def _find_pivots(series: pd.Series, left: int = 3, right: int = 3) -> List[float]:
    pivots: List[float] = []
    for index in range(left, len(series) - right):
        window = series.iloc[index - left : index + right + 1]
        candidate = series.iloc[index]
        if candidate == window.max() or candidate == window.min():
            pivots.append(candidate)
    return pivots


def detect_support_resistance(df: pd.DataFrame) -> Tuple[float, float]:
    highs = _find_pivots(df["high"], left=3, right=3)
    lows = _find_pivots(df["low"], left=3, right=3)

    if highs:
        resistance = sum(highs[-3:]) / len(highs[-3:])
    else:
        resistance = float(df["high"].rolling(20).max().iloc[-1])

    if lows:
        support = sum(lows[-3:]) / len(lows[-3:])
    else:
        support = float(df["low"].rolling(20).min().iloc[-1])

    return support, resistance


def _is_bullish_engulfing(last: pd.Series, prior: pd.Series) -> bool:
    return (
        prior["close"] < prior["open"]
        and last["close"] > last["open"]
        and last["close"] >= prior["open"]
        and last["open"] <= prior["close"]
    )


def _is_bearish_engulfing(last: pd.Series, prior: pd.Series) -> bool:
    return (
        prior["close"] > prior["open"]
        and last["close"] < last["open"]
        and last["open"] >= prior["close"]
        and last["close"] <= prior["open"]
    )


def _is_hammer(candle: pd.Series) -> bool:
    body = abs(candle["close"] - candle["open"])
    lower_shadow = min(candle["close"], candle["open"]) - candle["low"]
    upper_shadow = candle["high"] - max(candle["close"], candle["open"])
    return body > 0 and lower_shadow >= 2 * body and upper_shadow <= body


def _is_shooting_star(candle: pd.Series) -> bool:
    body = abs(candle["close"] - candle["open"])
    lower_shadow = min(candle["close"], candle["open"]) - candle["low"]
    upper_shadow = candle["high"] - max(candle["close"], candle["open"])
    return body > 0 and upper_shadow >= 2 * body and lower_shadow <= body


def _is_bullish_reversal(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    prior = df.iloc[-2]
    return _is_hammer(last) or _is_bullish_engulfing(last, prior)


def _is_bearish_reversal(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    prior = df.iloc[-2]
    return _is_shooting_star(last) or _is_bearish_engulfing(last, prior)


def _calculate_risk_targets(
    direction: str,
    entry: float,
    support: float,
    resistance: float,
    buffer: float = 0.003,
) -> Tuple[float, float]:
    if direction == "buy":
        stop_loss = max(support * (1 - buffer), entry * 0.995)
        risk = entry - stop_loss
        take_profit = entry + max(risk * 2.0, (resistance - entry) * 0.9)
    else:
        stop_loss = min(resistance * (1 + buffer), entry * 1.005)
        risk = stop_loss - entry
        take_profit = entry - max(risk * 2.0, (entry - support) * 0.9)
    return stop_loss, take_profit


def generate_trade_signal(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 20:
        return None

    support, resistance = detect_support_resistance(df)
    last = df.iloc[-1]
    prior = df.iloc[-2]
    avg_volume = float(df["volume"].rolling(20).mean().iloc[-1])
    close = float(last["close"])
    volume = float(last["volume"])
    price_range = resistance - support
    range_tolerance = max(price_range * 0.02, close * 0.001)

    if close > resistance and prior["close"] <= resistance and volume >= avg_volume * 1.2:
        stop_loss, take_profit = _calculate_risk_targets("buy", close, support, resistance)
        return {
            "type": "breakout",
            "direction": "buy",
            "entry": close,
            "support": support,
            "resistance": resistance,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": "Breakout above resistance with strong volume.",
        }

    if close < support and prior["close"] >= support and volume >= avg_volume * 1.2:
        stop_loss, take_profit = _calculate_risk_targets("sell", close, support, resistance)
        return {
            "type": "breakout",
            "direction": "sell",
            "entry": close,
            "support": support,
            "resistance": resistance,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": "Breakout below support with strong volume.",
        }

    if support + range_tolerance < close < resistance - range_tolerance:
        if abs(close - support) <= range_tolerance and _is_bullish_reversal(df):
            stop_loss, take_profit = _calculate_risk_targets("buy", close, support, resistance)
            return {
                "type": "range",
                "direction": "buy",
                "entry": close,
                "support": support,
                "resistance": resistance,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reason": "Range support touch with bullish reversal pattern.",
            }

        if abs(close - resistance) <= range_tolerance and _is_bearish_reversal(df):
            stop_loss, take_profit = _calculate_risk_targets("sell", close, support, resistance)
            return {
                "type": "range",
                "direction": "sell",
                "entry": close,
                "support": support,
                "resistance": resistance,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reason": "Range resistance touch with bearish reversal pattern.",
            }

    return None
