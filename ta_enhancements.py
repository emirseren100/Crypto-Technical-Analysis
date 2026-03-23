"""
Teknik Analiz Gelismeleri - 7 Kategori Uyumlu
1. Fiyat Hareketi  2. Scalp  3. MTF  4. Hacim/VWAP  5. Destek/Direnc
6. ML (feature)    7. Risk/Kalibrasyon
"""
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 4. Hacim ve VWAP
# ---------------------------------------------------------------------------

@dataclass
class VWAPBands:
    vwap: float
    upper_1std: float
    lower_1std: float
    upper_2std: float
    lower_2std: float
    price_vs_vwap: Literal["ustunde", "altinda", "icinde"]


def compute_vwap_bands(
    df: pd.DataFrame,
    vwap_series: pd.Series,
    std_period: int = 20,
) -> Optional[VWAPBands]:
    """VWAP +/- 1 ve 2 std bantlari. Fiyat band icinde/ustunde/altinda."""
    if len(df) < std_period or vwap_series is None or vwap_series.iloc[-1] != vwap_series.iloc[-1]:
        return None
    v = float(vwap_series.iloc[-1])
    typical = (df["high"] + df["low"] + df["close"]) / 3
    dev = typical.rolling(std_period).std().iloc[-1]
    if pd.isna(dev) or dev <= 0:
        return None
    u1 = v + dev
    l1 = v - dev
    u2 = v + 2 * dev
    l2 = v - 2 * dev
    close = float(df["close"].iloc[-1])
    if close > u1:
        pos = "ustunde"
    elif close < l1:
        pos = "altinda"
    else:
        pos = "icinde"
    return VWAPBands(vwap=v, upper_1std=u1, lower_1std=l1, upper_2std=u2, lower_2std=l2, price_vs_vwap=pos)


def volume_spike_ratio(vol: float, vol_avg: float, threshold: float = 1.5) -> bool:
    """Hacim spike: vol > vol_avg * threshold."""
    if vol_avg <= 0:
        return False
    return vol >= vol_avg * threshold


def obv_trend(obv_val: float, obv_ema: float) -> Literal["bullish", "bearish", "neutral"]:
    """OBV trend: EMA ustunde = bullish, altinda = bearish."""
    if obv_ema is None or obv_ema == 0:
        return "neutral"
    if obv_val > obv_ema * 1.02:
        return "bullish"
    if obv_val < obv_ema * 0.98:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# 3. Regime Detection (ADX)
# ---------------------------------------------------------------------------

@dataclass
class RegimeResult:
    regime: Literal["trending", "ranging"]
    adx: float
    strength: str  # "guclu", "orta", "zayif"


def atr_percentile(atr_series: pd.Series, lookback: int = 50) -> float:
    """
    ATR'nin son lookback mum icindeki yuzdelik dilimi (0-100).
    80+ = yuksek volatilite, 20- = dusuk volatilite.
    """
    if atr_series is None or len(atr_series) < lookback:
        return 50.0
    tail = atr_series.tail(lookback)
    current = float(tail.iloc[-1])
    if pd.isna(current) or current <= 0:
        return 50.0
    sorted_atr = tail.dropna().sort_values()
    n = len(sorted_atr)
    if n < 5:
        return 50.0
    rank = (sorted_atr < current).sum()
    return round(rank / n * 100, 1)


def detect_regime(adx: float, threshold_trend: float = 25.0, threshold_strong: float = 40.0) -> RegimeResult:
    """
    ADX > 25: Trending - momentum stratejisi.
    ADX < 25: Ranging - mean reversion.
    """
    if adx >= threshold_strong:
        strength = "guclu"
    elif adx >= threshold_trend:
        strength = "orta"
    else:
        strength = "zayif"
    regime = "trending" if adx >= threshold_trend else "ranging"
    return RegimeResult(regime=regime, adx=adx, strength=strength)


# ---------------------------------------------------------------------------
# 5. Destek/Direnc Gucluluk Skoru
# ---------------------------------------------------------------------------

def level_strength_score(touches: int, bars_since: int, proximity_pct: float) -> float:
    """
    Seviye gucluluk: touches (dokunma), yas (bars_since), yakınlık.
    Returns 0-1 skor.
    """
    touch_score = min(1.0, touches / 3) * 0.5
    age_score = max(0, 1 - bars_since / 100) * 0.3
    prox_score = max(0, 1 - proximity_pct / 2) * 0.2
    return min(1.0, touch_score + age_score + prox_score + 0.2)


# ---------------------------------------------------------------------------
# 1. Fiyat Hareketi - Mum Govde Orani
# ---------------------------------------------------------------------------

def candle_body_ratio_signal(df: pd.DataFrame, i: int) -> tuple[float, Literal["bullish", "bearish", "neutral"]]:
    """
    Body/range orani: 0.6+ = guclu mum, yon onay.
    Scalp icin: guclu govde = daha guvenilir sinyal.
    """
    if i < 0 or i >= len(df):
        return 0.0, "neutral"
    o = float(df["open"].iloc[i])
    h = float(df["high"].iloc[i])
    l = float(df["low"].iloc[i])
    c = float(df["close"].iloc[i])
    rng = h - l
    if rng <= 0:
        return 0.0, "neutral"
    body = abs(c - o)
    ratio = body / rng
    if c > o:
        direction = "bullish"
    elif c < o:
        direction = "bearish"
    else:
        direction = "neutral"
    return ratio, direction


# ---------------------------------------------------------------------------
# 2. Scalp Ozel - Mikro Yapi
# ---------------------------------------------------------------------------

def scalp_volume_confirm(vol: float, vol_avg: float, min_ratio: float = 1.2) -> bool:
    """Scalp: hacim onayi daha siki (1.2x)."""
    return volume_spike_ratio(vol, vol_avg, min_ratio)


def scalp_spread_filter(spread_bps: Optional[float], max_bps: float = 15.0) -> bool:
    """Scalp: spread > 15 bps = riskli, sinyal zayiflat."""
    if spread_bps is None:
        return True
    return spread_bps <= max_bps


# ---------------------------------------------------------------------------
# 7. Risk - Dinamik SL/TP Carpanlari
# ---------------------------------------------------------------------------

def dynamic_sl_multiplier(confidence: int, base: float = 1.5) -> float:
    """Guven 8+ = dar SL, 5-6 = genis SL."""
    if confidence >= 9:
        return base * 0.9
    if confidence >= 8:
        return base
    if confidence <= 5:
        return base * 1.2
    return base


def dynamic_tp_multiplier(confidence: int, rr_base: float = 1.0) -> float:
    """Guven yuksek = TP1 daha iddiali."""
    if confidence >= 9:
        return rr_base * 1.1
    if confidence <= 5:
        return rr_base * 0.9
    return rr_base
