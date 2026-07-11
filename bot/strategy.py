"""
Trading Strategy Module
EMA Crossover Strategy based on 4 EMAs (8, 13, 21, 55).

Rules (STRICT MODE):
- BUY  (LONG)  : EMA 55 crosses BELOW all other EMAs (8, 13, 21) -> 55 is the LOWEST line.
- SELL (SHORT) : EMA 55 crosses ABOVE all other EMAs (8, 13, 21) -> 55 is the HIGHEST line.
- HOLD         : otherwise

Cross Detection:
- `just_crossed` is True ONLY on the candle where the signal FIRST appears.
- If bot starts and line is ALREADY crossed, just_crossed = False (no trade).
- Bot must wait for a FRESH cross to enter a trade.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from .indicators import IndicatorSet

logger = logging.getLogger(__name__)


class Signal(str, Enum):
    BUY = "BUY"      # Go LONG
    SELL = "SELL"    # Go SHORT
    HOLD = "HOLD"    # No action


@dataclass
class StrategyResult:
    signal: Signal
    ema_8: float
    ema_13: float
    ema_21: float
    ema_55: float
    last_close: float
    reason: str
    just_crossed: bool = False   # True only on the FRESH candle where cross first happens


class EMAQuadStrategy:
    """Quad-EMA crossover strategy with cross-detection."""

    def __init__(self, ema_short=8, ema_mid1=13, ema_mid2=21, ema_long=55):
        self.indicators = IndicatorSet(ema_short, ema_mid1, ema_mid2, ema_long)
        self.ema_short = ema_short
        self.ema_mid1 = ema_mid1
        self.ema_mid2 = ema_mid2
        self.ema_long = ema_long
        # Track previous candle's signal to detect fresh crosses
        self._prev_signal: Signal = Signal.HOLD

    def analyze(self, df: pd.DataFrame) -> Optional[StrategyResult]:
        """Analyze the latest candle and return a signal."""
        if df is None or len(df) < self.ema_long + 5:
            logger.warning("Insufficient data for strategy (need >= %d rows, got %d)",
                           self.ema_long + 5, len(df) if df is not None else 0)
            return None

        enriched = self.indicators.compute(df)
        last = enriched.iloc[-1]

        e8, e13, e21, e55 = last["ema_8"], last["ema_13"], last["ema_21"], last["ema_55"]
        last_close = last["close"]

        if pd.isna(e55) or pd.isna(e8) or pd.isna(e13) or pd.isna(e21):
            logger.warning("EMA values contain NaN - need more historical data")
            return None

        # Determine current signal
        if e55 < e8 and e55 < e13 and e55 < e21:
            signal = Signal.BUY
            reason = "EMA55 is the BOTTOM line (below EMA8, EMA13, EMA21) -> LONG signal"
        elif e55 > e8 and e55 > e13 and e55 > e21:
            signal = Signal.SELL
            reason = "EMA55 is the TOP line (above EMA8, EMA13, EMA21) -> SHORT signal"
        else:
            signal = Signal.HOLD
            reason = "EMA55 is in a mixed position -> no action"

        # Detect FRESH cross: signal changed from HOLD or opposite signal to this signal
        just_crossed = False
        if signal in (Signal.BUY, Signal.SELL):
            if self._prev_signal != signal:
                just_crossed = True
        # Update previous signal state
        self._prev_signal = signal

        return StrategyResult(
            signal=signal,
            ema_8=float(e8),
            ema_13=float(e13),
            ema_21=float(e21),
            ema_55=float(e55),
            last_close=float(last_close),
            reason=reason,
            just_crossed=just_crossed,
        )

    def latest_indicators(self, df: pd.DataFrame) -> dict:
        """Return latest indicator values as a dict (for UI display)."""
        if df is None or len(df) < self.ema_long + 5:
            return {}
        enriched = self.indicators.compute(df)
        last = enriched.iloc[-1]
        return {
            "ema_8": float(last["ema_8"]) if not pd.isna(last["ema_8"]) else None,
            "ema_13": float(last["ema_13"]) if not pd.isna(last["ema_13"]) else None,
            "ema_21": float(last["ema_21"]) if not pd.isna(last["ema_21"]) else None,
            "ema_55": float(last["ema_55"]) if not pd.isna(last["ema_55"]) else None,
            "close": float(last["close"]),
            "timestamp": str(last.name) if last.name is not None else None,
        }

    def reset_cross_state(self):
        """Reset the previous-signal tracker. Called when bot starts or
        after a trade closes, so the next cross must be FRESH."""
        self._prev_signal = Signal.HOLD
