from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from data_fetcher import safe_fetch_klines
from indicators import compute_all
from price_action import detect_trend

TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# MTF agirliklari: 4h > 1h > 15m > 1d (buyuk resim onemli)
TF_WEIGHTS = {"1d": 1.0, "4h": 1.5, "1h": 1.2, "15m": 1.0, "5m": 0.8}


@dataclass
class TimeframeAnalysis:
    interval: str
    trend: Literal["up", "down", "sideways"]
    rsi: float
    macd_hist: float
    above_ema50: bool
    bb_position: Literal["above", "inside", "below"]
    close: float = 0.0
    adx: float = 0.0


@dataclass
class MTFResult:
    symbol: str
    analyses: list[TimeframeAnalysis] = field(default_factory=list)
    consensus: Literal["LONG", "SHORT", "BEKLE"] = "BEKLE"
    alignment_score: int = 0
    summary: str = ""
    current_price: float = 0.0
    regime: Literal["trending", "ranging"] = "trending"


def analyze_single_tf(df: pd.DataFrame, interval: str) -> TimeframeAnalysis | None:
    if df.empty or len(df) < 50:
        return None

    data = compute_all(df)
    last = data.iloc[-1]
    trend = detect_trend(df)

    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    macd_h = float(last["macd_hist"]) if pd.notna(last["macd_hist"]) else 0.0
    adx_val = float(last["adx"]) if "adx" in last.index and pd.notna(last.get("adx")) else 25.0
    close = float(last["close"])
    ema50 = float(last["ema_50"]) if pd.notna(last["ema_50"]) else close
    bb_upper = float(last["bb_upper"]) if pd.notna(last["bb_upper"]) else close + 1
    bb_lower = float(last["bb_lower"]) if pd.notna(last["bb_lower"]) else close - 1

    if close >= bb_upper:
        bb_pos = "above"
    elif close <= bb_lower:
        bb_pos = "below"
    else:
        bb_pos = "inside"

    return TimeframeAnalysis(
        interval=interval,
        trend=trend,
        rsi=rsi_val,
        macd_hist=macd_h,
        above_ema50=close > ema50,
        bb_position=bb_pos,
        close=close,
        adx=adx_val,
    )


def run_mtf_analysis(
    symbol: str,
    timeframes: list[str] | None = None,
    limit: int = 300,
) -> MTFResult:
    if timeframes is None:
        timeframes = TIMEFRAMES

    result = MTFResult(symbol=symbol)
    bullish_weighted = 0.0
    bearish_weighted = 0.0
    total_weight = 0.0

    for tf in timeframes:
        try:
            df = safe_fetch_klines(symbol, tf, limit)
        except Exception:
            continue

        analysis = analyze_single_tf(df, tf)
        if analysis is None:
            continue

        result.analyses.append(analysis)

        weight = TF_WEIGHTS.get(tf, 1.0)
        total_weight += weight

        score = 0
        if analysis.trend == "up":
            score += 1
        elif analysis.trend == "down":
            score -= 1

        if analysis.rsi < 35:
            score += 1
        elif analysis.rsi > 70:
            score -= 1

        if analysis.macd_hist > 0:
            score += 1
        elif analysis.macd_hist < 0:
            score -= 1

        if analysis.above_ema50:
            score += 1
        else:
            score -= 1

        if score >= 2:
            bullish_weighted += weight
        elif score <= -2:
            bearish_weighted += weight

    total = len(result.analyses)
    if total == 0:
        result.summary = "Veri alinamadi"
        return result

    result.current_price = result.analyses[-1].close if result.analyses else 0.0

    if total_weight <= 0:
        result.summary = "Veri alinamadi"
        return result

    avg_adx = sum(a.adx for a in result.analyses) / total if total > 0 else 25
    result.regime = "trending" if avg_adx >= 25 else "ranging"

    bull_ratio = bullish_weighted / total_weight
    bear_ratio = bearish_weighted / total_weight

    if bull_ratio >= 0.55:
        result.consensus = "LONG"
        result.alignment_score = int(bullish_weighted * 10)
        result.summary = f"Agirlikli {bull_ratio:.0%} yukselis ({bullish_weighted:.1f}/{total_weight:.1f})"
    elif bear_ratio >= 0.55:
        result.consensus = "SHORT"
        result.alignment_score = int(bearish_weighted * 10)
        result.summary = f"Agirlikli {bear_ratio:.0%} dusus ({bearish_weighted:.1f}/{total_weight:.1f})"
    else:
        result.consensus = "BEKLE"
        result.alignment_score = 0
        result.summary = f"Zaman dilimleri uyumsuz (yukselis:{bull_ratio:.0%} dusus:{bear_ratio:.0%})"

    return result
