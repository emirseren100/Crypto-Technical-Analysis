"""
Korelasyon Analizi - BTC trend ve Dominance
BTC sert dusus = alt long riskli
Dominance yukselis = alt long riskli (paradan cikis)
"""
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from data_fetcher import fetch_btc_dominance, safe_fetch_klines
from price_action import detect_trend


@dataclass
class CorrelationResult:
    btc_trend: Literal["up", "down", "sideways"]
    btc_trend_strength: float  # 0-1
    dominance: Optional[float]
    dominance_rising: bool
    alt_long_risky: bool
    alt_short_risky: bool
    reason: str


def get_btc_trend(interval: str = "1h", limit: int = 100) -> tuple[Literal["up", "down", "sideways"], float]:
    """BTC trend - scalp icin 15m, normal icin 1h."""
    try:
        from indicators import compute_all
        df = safe_fetch_klines("BTCUSDT", interval, limit)
        if len(df) < 50:
            return "sideways", 0.0
        data = compute_all(df, scalp=(interval in ("5m", "15m")))
        trend = detect_trend(df, data_with_indicators=data)
        close = float(df["close"].iloc[-1])
        ema50 = float(data["ema_50"].iloc[-1]) if pd.notna(data["ema_50"].iloc[-1]) else close
        strength = abs(close - ema50) / ema50 * 100 if ema50 > 0 else 0
        strength = min(1.0, strength / 2)
        return trend, strength
    except Exception:
        return "sideways", 0.0


def get_dominance_trend() -> tuple[Optional[float], bool]:
    """Dominance ve yukselis mi. Cache yok - her cagrida API."""
    try:
        dom = fetch_btc_dominance()
        if dom is None:
            return None, False
        return dom, False
    except Exception:
        return None, False


_cached_dom: Optional[float] = None
_cached_dom_prev: Optional[float] = None


def analyze_correlation(
    symbol: str,
    direction: Literal["LONG", "SHORT"],
    interval: str,
    scalp: bool = False,
) -> CorrelationResult:
    """
    Alt coin icin: LONG sinyali + BTC bearish = riskli.
    Alt coin icin: LONG sinyali + Dominance yukselis = riskli.
    """
    btc_interval = "15m" if scalp else "1h"
    btc_trend, btc_strength = get_btc_trend(btc_interval, 100)
    dominance, dom_rising = get_dominance_trend()

    alt_long_risky = False
    alt_short_risky = False
    reasons: list[str] = []

    if symbol == "BTCUSDT":
        return CorrelationResult(
            btc_trend=btc_trend,
            btc_trend_strength=btc_strength,
            dominance=dominance,
            dominance_rising=dom_rising,
            alt_long_risky=False,
            alt_short_risky=False,
            reason="BTC - korelasyon uygulanmaz",
        )

    if direction == "LONG":
        if btc_trend == "down":
            alt_long_risky = True
            reasons.append("BTC dusus - alt long riskli")
        elif btc_trend == "down" and btc_strength > 0.5:
            alt_long_risky = True
            reasons.append("BTC guclu dusus")
        if dominance is not None and dominance > 55:
            reasons.append(f"BTC Dom %{dominance:.1f} - yuksek")
        if dominance is not None and dominance > 58:
            alt_long_risky = True
            reasons.append("Dominance cok yuksek - alt long riskli")
    else:
        if btc_trend == "up" and btc_strength > 0.5:
            alt_short_risky = True
            reasons.append("BTC guclu yukselis - alt short riskli")
        if dominance is not None and dominance < 45:
            reasons.append(f"BTC Dom %{dominance:.1f} - dusuk, alt pump olabilir")

    reason_str = "; ".join(reasons) if reasons else "Korelasyon uygun"
    return CorrelationResult(
        btc_trend=btc_trend,
        btc_trend_strength=btc_strength,
        dominance=dominance,
        dominance_rising=dom_rising,
        alt_long_risky=alt_long_risky,
        alt_short_risky=alt_short_risky,
        reason=reason_str,
    )
