from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Support / Resistance
# ---------------------------------------------------------------------------

@dataclass
class Level:
    price: float
    kind: Literal["support", "resistance"]
    touches: int = 1


def find_support_resistance(
    df: pd.DataFrame,
    window: int = 5,
    merge_pct: float = 0.5,
) -> list[Level]:
    """PDF (Bucek, Galen Woods): Swing noktalari + lokal min/max ile destek/direnc."""
    return find_support_resistance_with_swings(df, window, merge_pct)


def _merge_levels(
    levels: list[Level],
    ref_price: float,
    pct: float,
) -> list[Level]:
    if not levels:
        return []

    threshold = ref_price * pct / 100
    levels_sorted = sorted(levels, key=lambda lv: lv.price)
    merged: list[Level] = [levels_sorted[0]]

    for lv in levels_sorted[1:]:
        prev = merged[-1]
        if abs(lv.price - prev.price) < threshold:
            prev.touches += 1
            prev.price = (prev.price + lv.price) / 2
        else:
            merged.append(lv)

    return merged


def compute_pivot_points(df: pd.DataFrame) -> dict[str, float] | None:
    """Klasik pivot: onceki mum H,L,C. P, R1, R2, S1, S2."""
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
    p = (h + l + c) / 3
    r1 = 2 * p - l
    r2 = p + (h - l)
    s1 = 2 * p - h
    s2 = p - (h - l)
    return {"pivot": p, "r1": r1, "r2": r2, "s1": s1, "s2": s2}


def nearest_support_resistance(
    levels: list[Level],
    price: float,
) -> tuple[Level | None, Level | None]:
    support = None
    resistance = None
    for lv in levels:
        if lv.price <= price:
            if support is None or lv.price > support.price:
                support = lv
        else:
            if resistance is None or lv.price < resistance.price:
                resistance = lv
    return support, resistance


# ---------------------------------------------------------------------------
# Candlestick patterns
# ---------------------------------------------------------------------------

@dataclass
class CandlePattern:
    index: int
    name: str
    direction: Literal["bullish", "bearish", "neutral"]


def detect_patterns(df: pd.DataFrame) -> list[CandlePattern]:
    """PDF (Galen Woods, Bucek) kurallari: Hammer/Shooting Star >=0.7 range, Inside Bar, Harami."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    patterns: list[CandlePattern] = []

    for i in range(1, len(df)):
        body = abs(c[i] - o[i])
        candle_range = h[i] - l[i]
        if candle_range == 0:
            continue

        upper_wick = h[i] - max(o[i], c[i])
        lower_wick = min(o[i], c[i]) - l[i]
        body_ratio = body / candle_range

        # --- Doji (Galen Woods) ---
        if body_ratio < 0.1:
            patterns.append(CandlePattern(i, "doji", "neutral"))
            continue

        # --- Hammer (Bucek: (min(OP,CL)-LOW)/(HIGH-LOW) >= 0.7) ---
        lower_wick_ratio = lower_wick / candle_range
        if lower_wick_ratio >= 0.7 and upper_wick < candle_range * 0.2:
            patterns.append(CandlePattern(i, "hammer", "bullish"))

        # --- Shooting Star (Bucek: (HIGH-max(OP,CL))/(HIGH-LOW) >= 0.7) ---
        upper_wick_ratio = upper_wick / candle_range
        if upper_wick_ratio >= 0.7 and lower_wick < candle_range * 0.2:
            patterns.append(CandlePattern(i, "shooting_star", "bearish"))

        # --- Inside Bar (Galen Woods: range icinde) ---
        if i >= 1 and h[i] < h[i - 1] and l[i] > l[i - 1]:
            patterns.append(CandlePattern(i, "inside_bar", "neutral"))

        # --- NR7 (Galen Woods: son 7 mumun en dar range'i - breakout oncesi) ---
        if i >= 7:
            r7 = [h[j] - l[j] for j in range(i - 6, i + 1)]
            if r7[-1] == min(r7) and r7[-1] > 0:
                patterns.append(CandlePattern(i, "nr7", "neutral"))

        # --- Inside Bar Breakout (Galen Woods: parent high/low kirilirsa yon) ---
        if i >= 2 and h[i - 1] < h[i - 2] and l[i - 1] > l[i - 2]:
            parent_high, parent_low = h[i - 2], l[i - 2]
            if c[i] > parent_high:
                patterns.append(CandlePattern(i, "inside_bar_bullish_breakout", "bullish"))
            elif c[i] < parent_low:
                patterns.append(CandlePattern(i, "inside_bar_bearish_breakout", "bearish"))

        # --- Bullish engulfing ---
        if (
            i >= 1
            and c[i - 1] < o[i - 1]
            and c[i] > o[i]
            and o[i] <= c[i - 1]
            and c[i] >= o[i - 1]
        ):
            patterns.append(CandlePattern(i, "bullish_engulfing", "bullish"))

        # --- Bearish engulfing ---
        if (
            i >= 1
            and c[i - 1] > o[i - 1]
            and c[i] < o[i]
            and o[i] >= c[i - 1]
            and c[i] <= o[i - 1]
        ):
            patterns.append(CandlePattern(i, "bearish_engulfing", "bearish"))

        # --- Harami (Galen Woods: kucuk mum oncekinin govdesi icinde) ---
        if i >= 1:
            o_prev, c_prev = o[i - 1], c[i - 1]
            if o[i] < c[i] and c_prev < o_prev:  # curr bullish, prev bearish
                if o[i] > c_prev and c[i] < o_prev:  # curr body inside prev body
                    patterns.append(CandlePattern(i, "bullish_harami", "bullish"))
            elif o[i] > c[i] and c_prev > o_prev:  # curr bearish, prev bullish
                if o[i] < c_prev and c[i] > o_prev:  # curr body inside prev body
                    patterns.append(CandlePattern(i, "bearish_harami", "bearish"))

        # --- Three White Soldiers (3 ardışık güçlü yeşil mum) ---
        if i >= 2:
            if (
                c[i] > o[i] and c[i - 1] > o[i - 1] and c[i - 2] > o[i - 2]
                and c[i] > c[i - 1] > c[i - 2]
                and o[i] > o[i - 1] > o[i - 2]
                and body_ratio >= 0.5
            ):
                patterns.append(CandlePattern(i, "three_white_soldiers", "bullish"))

        # --- Three Black Crows (3 ardışık güçlü kırmızı mum) ---
        if i >= 2:
            if (
                c[i] < o[i] and c[i - 1] < o[i - 1] and c[i - 2] < o[i - 2]
                and c[i] < c[i - 1] < c[i - 2]
                and o[i] < o[i - 1] < o[i - 2]
                and body_ratio >= 0.5
            ):
                patterns.append(CandlePattern(i, "three_black_crows", "bearish"))

        # --- Morning Star (3 mumlü yükseliş reversal: büyük kırmızı, küçük, büyük yeşil) ---
        if i >= 2:
            body_0 = abs(c[i] - o[i])
            body_1 = abs(c[i - 1] - o[i - 1])
            body_2 = abs(c[i - 2] - o[i - 2])
            rng_0, rng_1, rng_2 = h[i] - l[i], h[i - 1] - l[i - 1], h[i - 2] - l[i - 2]
            if rng_0 > 0 and rng_1 > 0 and rng_2 > 0:
                br0 = body_0 / rng_0
                br1 = body_1 / rng_1
                br2 = body_2 / rng_2
                if (
                    c[i - 2] < o[i - 2]
                    and br2 >= 0.5
                    and br1 < 0.3
                    and c[i] > o[i]
                    and br0 >= 0.5
                    and c[i] > (o[i - 2] + c[i - 2]) / 2
                ):
                    patterns.append(CandlePattern(i, "morning_star", "bullish"))

        # --- Evening Star (3 mumlü düşüş reversal) ---
        if i >= 2:
            body_0 = abs(c[i] - o[i])
            body_1 = abs(c[i - 1] - o[i - 1])
            body_2 = abs(c[i - 2] - o[i - 2])
            rng_0, rng_1, rng_2 = h[i] - l[i], h[i - 1] - l[i - 1], h[i - 2] - l[i - 2]
            if rng_0 > 0 and rng_1 > 0 and rng_2 > 0:
                br0 = body_0 / rng_0
                br1 = body_1 / rng_1
                br2 = body_2 / rng_2
                if (
                    c[i - 2] > o[i - 2]
                    and br2 >= 0.5
                    and br1 < 0.3
                    and c[i] < o[i]
                    and br0 >= 0.5
                    and c[i] < (o[i - 2] + c[i - 2]) / 2
                ):
                    patterns.append(CandlePattern(i, "evening_star", "bearish"))

    return patterns


def detect_swing_lows(df: pd.DataFrame, lookback: int = 2) -> list[int]:
    """Bucek: Swing low = kirmizi mum + yesil mum (dusus sonrasi donus)."""
    o = df["open"].values
    c = df["close"].values
    low = df["low"].values
    idxs: list[int] = []
    for i in range(lookback, len(df) - 1):
        # onceki kirmizi (c < o), sonraki yesil (c > o)
        if c[i - 1] < o[i - 1] and c[i] > o[i]:
            if low[i] <= low[i - 1] and low[i] <= low[i + 1]:
                idxs.append(i)
    return idxs


def detect_swing_highs(df: pd.DataFrame, lookback: int = 2) -> list[int]:
    """Bucek: Swing high = yesil mum + kirmizi mum (yukari sonrasi donus)."""
    o = df["open"].values
    c = df["close"].values
    high = df["high"].values
    idxs: list[int] = []
    for i in range(lookback, len(df) - 1):
        if c[i - 1] > o[i - 1] and c[i] < o[i]:
            if high[i] >= high[i - 1] and high[i] >= high[i + 1]:
                idxs.append(i)
    return idxs


def find_support_resistance_with_swings(
    df: pd.DataFrame,
    window: int = 5,
    merge_pct: float = 0.5,
) -> list[Level]:
    """PDF: Swing noktalarini destek/direnc olarak kullan (Bucek, Galen Woods)."""
    levels: list[Level] = []
    close_last = float(df["close"].iloc[-1])

    for i in detect_swing_lows(df):
        levels.append(Level(price=float(df["low"].iloc[i]), kind="support"))
    for i in detect_swing_highs(df):
        levels.append(Level(price=float(df["high"].iloc[i]), kind="resistance"))

    # Klasik local min/max da ekle
    high = df["high"].values
    low = df["low"].values
    for i in range(window, len(df) - window):
        if low[i] == np.min(low[i - window : i + window + 1]):
            levels.append(Level(price=float(low[i]), kind="support"))
        if high[i] == np.max(high[i - window : i + window + 1]):
            levels.append(Level(price=float(high[i]), kind="resistance"))

    merged = _merge_levels(levels, close_last, merge_pct)
    return sorted(merged, key=lambda lv: lv.price)


def find_support_resistance_extended(
    df: pd.DataFrame,
    window: int = 5,
    merge_pct: float = 0.5,
    include_smc: bool = True,
    include_fib: bool = True,
    include_volume_profile: bool = True,
    include_session_opens: bool = True,
) -> list[Level]:
    """Genisletilmis S/R: swing, pivot, SMC (FVG/OB), Fibonacci, volume profile, session open."""
    levels = find_support_resistance_with_swings(df, window, merge_pct)
    close_last = float(df["close"].iloc[-1])

    if include_smc:
        try:
            from smc import detect_fair_value_gaps, detect_order_blocks
            fvgs = detect_fair_value_gaps(df, lookback=30)
            for fvg in fvgs:
                if not fvg.filled or fvg.fill_pct < 0.8:
                    mid = (fvg.top + fvg.bottom) / 2
                    kind = "support" if fvg.direction == "bullish" else "resistance"
                    levels.append(Level(price=mid, kind=kind, touches=2))
            obs = detect_order_blocks(df, lookback=30)
            for ob in obs:
                kind = "support" if ob.direction == "bullish" else "resistance"
                levels.append(Level(price=ob.price, kind=kind, touches=2))
        except ImportError:
            pass

    if include_fib:
        fib_levels = compute_fibonacci_levels(df, lookback=50)
        if fib_levels:
            for name, price in fib_levels.items():
                kind = "support" if price < close_last else "resistance"
                levels.append(Level(price=price, kind=kind, touches=2))

    if include_volume_profile:
        vp = compute_volume_profile(df, lookback=100)
        if vp:
            for key in ("poc", "vah", "val"):
                p = vp.get(key)
                if p is not None:
                    kind = "support" if p < close_last else "resistance"
                    levels.append(Level(price=float(p), kind=kind, touches=2))

    if include_session_opens:
        session_levels = get_session_open_levels(df, lookback=48)
        for sl in session_levels:
            levels.append(Level(price=sl["price"], kind=sl["kind"], touches=2))

    merged = _merge_levels(levels, close_last, merge_pct)
    return sorted(merged, key=lambda lv: lv.price)


# ---------------------------------------------------------------------------
# Fibonacci Retracement
# ---------------------------------------------------------------------------

def compute_fibonacci_levels(
    df: pd.DataFrame,
    lookback: int = 50,
    levels: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786),
) -> dict[str, float] | None:
    """Swing high/low arasinda Fibonacci seviyeleri."""
    if len(df) < 10:
        return None
    sl = detect_swing_lows(df, lookback=2)
    sh = detect_swing_highs(df, lookback=2)
    if not sl or not sh:
        return None
    start = max(0, len(df) - lookback)
    sl_in = [i for i in sl if start <= i < len(df)]
    sh_in = [i for i in sh if start <= i < len(df)]
    if not sl_in or not sh_in:
        return None
    low_idx = min(sl_in, key=lambda i: float(df["low"].iloc[i]))
    high_idx = max(sh_in, key=lambda i: float(df["high"].iloc[i]))
    swing_low = float(df["low"].iloc[low_idx])
    swing_high = float(df["high"].iloc[high_idx])
    if swing_high <= swing_low:
        return None
    diff = swing_high - swing_low
    result: dict[str, float] = {}
    for fib in levels:
        retrace = swing_high - diff * fib
        result[f"fib_{fib}"] = retrace
    return result


# ---------------------------------------------------------------------------
# Volume Profile (POC, VAH, VAL)
# ---------------------------------------------------------------------------

def compute_volume_profile(
    df: pd.DataFrame,
    lookback: int = 100,
    value_area_pct: float = 0.68,
    num_bins: int = 50,
) -> dict[str, float] | None:
    """POC (Point of Control), VAH (Value Area High), VAL (Value Area Low)."""
    if len(df) < 20 or "volume" not in df.columns:
        return None
    recent = df.iloc[-lookback:]
    low = float(recent["low"].min())
    high = float(recent["high"].max())
    if high <= low:
        return None
    bins = np.linspace(low, high, num_bins + 1)
    vol_profile = np.zeros(num_bins)
    for _, row in recent.iterrows():
        h, l, c, v = float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])
        typical = (h + l + c) / 3
        idx = np.clip(int((typical - low) / (high - low) * num_bins), 0, num_bins - 1)
        vol_profile[idx] += v
    if vol_profile.sum() <= 0:
        return None
    poc_idx = int(np.argmax(vol_profile))
    poc = float((bins[poc_idx] + bins[poc_idx + 1]) / 2)
    target_vol = vol_profile.sum() * value_area_pct
    cum = vol_profile[poc_idx]
    va_low, va_high = poc_idx, poc_idx
    while cum < target_vol and (va_low > 0 or va_high < num_bins - 1):
        v_l = vol_profile[va_low - 1] if va_low > 0 else 0
        v_h = vol_profile[va_high + 1] if va_high < num_bins - 1 else 0
        if v_l >= v_h and va_low > 0:
            va_low -= 1
            cum += vol_profile[va_low]
        elif va_high < num_bins - 1:
            va_high += 1
            cum += vol_profile[va_high]
        else:
            break
    val = float((bins[va_low] + bins[va_low + 1]) / 2)
    vah = float((bins[va_high] + bins[va_high + 1]) / 2)
    return {"poc": poc, "vah": vah, "val": val}


# ---------------------------------------------------------------------------
# Session Open Levels (Asian, London, NY)
# ---------------------------------------------------------------------------

def get_session_open_levels(
    df: pd.DataFrame,
    lookback: int = 48,
) -> list[dict]:
    """Seans acilis mumlarinin open seviyeleri - destek/direnc (Asian 00UTC, London 08UTC, NY 13/14UTC)."""
    if len(df) < 10:
        return []
    try:
        recent = df.iloc[-lookback:]
        close_last = float(df["close"].iloc[-1])
        result: list[dict] = []
        seen: set[int] = set()
        for idx in recent.index:
            h = idx.hour if hasattr(idx, "hour") else getattr(idx, "hour", None)
            if h is None:
                continue
            if h == 0 and 0 not in seen:
                seen.add(0)
                price = float(recent.loc[idx, "open"])
                kind = "support" if price < close_last else "resistance"
                result.append({"price": price, "kind": kind, "session": "asian"})
            elif h == 8 and 8 not in seen:
                seen.add(8)
                price = float(recent.loc[idx, "open"])
                kind = "support" if price < close_last else "resistance"
                result.append({"price": price, "kind": kind, "session": "london"})
            elif h in (13, 14) and 13 not in seen:
                seen.add(13)
                price = float(recent.loc[idx, "open"])
                kind = "support" if price < close_last else "resistance"
                result.append({"price": price, "kind": kind, "session": "ny"})
    except Exception:
        return []
    return result


# ---------------------------------------------------------------------------
# Volume Confirmation
# ---------------------------------------------------------------------------

def check_volume_confirmation(
    df: pd.DataFrame,
    idx: int,
    vol_avg: float,
    min_ratio: float = 1.2,
) -> tuple[bool, float]:
    """Sinyal mumunda hacim onayi. Returns: (passed, actual_ratio)."""
    if idx < 0 or idx >= len(df) or "volume" not in df.columns:
        return True, 1.0
    vol = float(df["volume"].iloc[idx])
    if vol_avg <= 0:
        return True, 1.0
    ratio = vol / vol_avg
    return ratio >= min_ratio, ratio


# ---------------------------------------------------------------------------
# Trend structure  (higher highs / lower lows)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# RSI / Fiyat Divergence
# ---------------------------------------------------------------------------

@dataclass
class Divergence:
    kind: Literal["bullish", "bearish"]
    price1: float
    price2: float
    rsi1: float
    rsi2: float
    idx1: int
    idx2: int


def detect_rsi_divergence(
    df: pd.DataFrame,
    rsi_series: pd.Series,
    lookback: int = 30,
    swing_window: int = 3,
) -> Divergence | None:
    """RSI-Fiyat divergence. Bullish: fiyat dusus, RSI yukselis. Bearish: tersi."""
    if len(df) < lookback or len(rsi_series) < lookback:
        return None

    low = df["low"].values
    high = df["high"].values
    rsi = rsi_series.values
    n = len(df)

    def is_swing_low(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if low[i] >= low[i - j] or low[i] >= low[i + j]:
                return False
        return True

    def is_swing_high(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if high[i] <= high[i - j] or high[i] <= high[i + j]:
                return False
        return True

    start = n - lookback
    swing_lows = [(i, low[i], rsi[i]) for i in range(start, n - swing_window) if is_swing_low(i)]
    swing_highs = [(i, high[i], rsi[i]) for i in range(start, n - swing_window) if is_swing_high(i)]

    if len(swing_lows) >= 2:
        (i1, p1, r1), (i2, p2, r2) = swing_lows[-2], swing_lows[-1]
        if p2 < p1 and r2 > r1:
            return Divergence("bullish", float(p1), float(p2), float(r1), float(r2), i1, i2)

    if len(swing_highs) >= 2:
        (i1, p1, r1), (i2, p2, r2) = swing_highs[-2], swing_highs[-1]
        if p2 > p1 and r2 < r1:
            return Divergence("bearish", float(p1), float(p2), float(r1), float(r2), i1, i2)

    return None


# ---------------------------------------------------------------------------
# MACD Divergence
# ---------------------------------------------------------------------------

@dataclass
class MacdDivergence:
    kind: Literal["bullish", "bearish"]
    price1: float
    price2: float
    macd1: float
    macd2: float
    idx1: int
    idx2: int


def detect_liquidity_grab(
    df: pd.DataFrame,
    support: Optional[object],
    resistance: Optional[object],
    close: float,
    lookback: int = 10,
    wick_ratio_min: float = 0.5,
) -> tuple[bool, bool]:
    """
    Likidite grab: Fiyat destek/direnc altinda/ustunde wick yapti, sonra geri dondu.
    Returns: (bullish_grab, bearish_grab)
    Bullish: Son mumlar destek altina wick yapti, close destek ustunde.
    Bearish: Son mumlar direnc ustune wick yapti, close direnc altinda.
    """
    if len(df) < lookback:
        return False, False
    bullish = False
    bearish = False
    low = df["low"].values
    high = df["high"].values
    o = df["open"].values
    c = df["close"].values
    n = len(df)
    start = n - lookback

    if support and getattr(support, "price", 0) > 0:
        for i in range(start, n):
            rng = high[i] - low[i]
            if rng <= 0:
                continue
            lower_wick = min(o[i], c[i]) - low[i]
            sp = getattr(support, "price", 0)
            if lower_wick / rng >= wick_ratio_min and low[i] < sp * 1.002:
                if c[i] > sp and close > sp:
                    bullish = True
                    break

    if resistance and getattr(resistance, "price", 0) > 0:
        for i in range(start, n):
            rng = high[i] - low[i]
            if rng <= 0:
                continue
            upper_wick = high[i] - max(o[i], c[i])
            rp = getattr(resistance, "price", 0)
            if upper_wick / rng >= wick_ratio_min and high[i] > rp * 0.998:
                if c[i] < rp and close < rp:
                    bearish = True
                    break

    return bullish, bearish


def detect_obv_divergence(
    df: pd.DataFrame,
    obv_series: pd.Series,
    lookback: int = 30,
    swing_window: int = 3,
) -> Optional[Literal["bullish", "bearish"]]:
    """
    OBV-Fiyat divergence. Bullish: fiyat dusus (LL), OBV yukselis (HL).
    Bearish: fiyat yukselis (HH), OBV dusus (LH).
    """
    if len(df) < lookback or len(obv_series) < lookback:
        return None
    low = df["low"].values
    high = df["high"].values
    obv = obv_series.values
    n = len(df)

    def is_swing_low(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if low[i] >= low[i - j] or low[i] >= low[i + j]:
                return False
        return True

    def is_swing_high(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if high[i] <= high[i - j] or high[i] <= high[i + j]:
                return False
        return True

    start = n - lookback
    swing_lows = [(i, low[i], obv[i]) for i in range(start, n - swing_window) if is_swing_low(i)]
    swing_highs = [(i, high[i], obv[i]) for i in range(start, n - swing_window) if is_swing_high(i)]

    if len(swing_lows) >= 2:
        (i1, p1, o1), (i2, p2, o2) = swing_lows[-2], swing_lows[-1]
        if p2 < p1 and o2 > o1:
            return "bullish"

    if len(swing_highs) >= 2:
        (i1, p1, o1), (i2, p2, o2) = swing_highs[-2], swing_highs[-1]
        if p2 > p1 and o2 < o1:
            return "bearish"

    return None


def detect_macd_divergence(
    df: pd.DataFrame,
    macd_hist_series: pd.Series,
    lookback: int = 30,
    swing_window: int = 3,
) -> MacdDivergence | None:
    """MACD histogram - fiyat divergence. Bullish: fiyat dusus, MACD yukselis."""
    if len(df) < lookback or len(macd_hist_series) < lookback:
        return None
    low = df["low"].values
    high = df["high"].values
    macd = macd_hist_series.values
    n = len(df)

    def is_swing_low(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if low[i] >= low[i - j] or low[i] >= low[i + j]:
                return False
        return True

    def is_swing_high(i: int) -> bool:
        if i < swing_window or i >= n - swing_window:
            return False
        for j in range(1, swing_window + 1):
            if high[i] <= high[i - j] or high[i] <= high[i + j]:
                return False
        return True

    start = n - lookback
    swing_lows = [(i, low[i], macd[i]) for i in range(start, n - swing_window) if is_swing_low(i)]
    swing_highs = [(i, high[i], macd[i]) for i in range(start, n - swing_window) if is_swing_high(i)]

    if len(swing_lows) >= 2:
        (i1, p1, m1), (i2, p2, m2) = swing_lows[-2], swing_lows[-1]
        if p2 < p1 and m2 > m1:
            return MacdDivergence("bullish", float(p1), float(p2), float(m1), float(m2), i1, i2)

    if len(swing_highs) >= 2:
        (i1, p1, m1), (i2, p2, m2) = swing_highs[-2], swing_highs[-1]
        if p2 > p1 and m2 < m1:
            return MacdDivergence("bearish", float(p1), float(p2), float(m1), float(m2), i1, i2)

    return None


# ---------------------------------------------------------------------------
# Structure Break (BOS / CHoCH) - Market Structure wrapper
# ---------------------------------------------------------------------------

def detect_structure_break(
    df: pd.DataFrame,
    lookback: int = 50,
    scalp: bool = False,
) -> dict:
    """BOS (Break of Structure) ve CHoCH (Change of Character) - market_structure wrapper."""
    try:
        from market_structure import detect_market_structure
        ms = detect_market_structure(df, lookback=lookback, scalp=scalp)
        return {
            "trend": ms.trend,
            "last_bos": ms.last_bos,
            "last_bos_idx": ms.last_bos_idx,
            "choch": ms.choch,
            "structure": ms.structure,
        }
    except ImportError:
        return {"trend": "ranging", "last_bos": "", "last_bos_idx": -1, "choch": "", "structure": ""}


# ---------------------------------------------------------------------------
# Chart patterns (Galen Woods: Double Top/Bottom, Head & Shoulders)
# ---------------------------------------------------------------------------

@dataclass
class ChartPattern:
    name: str
    direction: Literal["bullish", "bearish"]
    level: float
    idx: int


def detect_chart_patterns(
    df: pd.DataFrame,
    lookback: int = 50,
    tolerance_pct: float = 1.0,
) -> list[ChartPattern]:
    """Double Top, Double Bottom, Head & Shoulders (Galen Woods)."""
    if len(df) < 20:
        return []
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    start = max(0, n - lookback)
    patterns: list[ChartPattern] = []

    def _near(a: float, b: float, pct: float) -> bool:
        if a == 0:
            return abs(b - a) < pct
        return abs(a - b) / a * 100 < pct

    # Swing highs/lows
    sh = [i for i in range(start + 2, n - 2) if h[i] >= h[i - 1] and h[i] >= h[i - 2] and h[i] >= h[i + 1] and h[i] >= h[i + 2]]
    sl = [i for i in range(start + 2, n - 2) if l[i] <= l[i - 1] and l[i] <= l[i - 2] and l[i] <= l[i + 1] and l[i] <= l[i + 2]]

    # Double Top: iki swing high yakin seviyede
    for i, j in zip(sh[:-1], sh[1:]):
        if j - i < 5:
            continue
        if _near(h[i], h[j], tolerance_pct):
            patterns.append(ChartPattern("double_top", "bearish", float(h[i]), j))

    # Double Bottom: iki swing low yakin seviyede
    for i, j in zip(sl[:-1], sl[1:]):
        if j - i < 5:
            continue
        if _near(l[i], l[j], tolerance_pct):
            patterns.append(ChartPattern("double_bottom", "bullish", float(l[i]), j))

    # Head & Shoulders: 3 swing high - ortadaki (bas) en yuksek, omuzlar benzer
    if len(sh) >= 3:
        for i in range(len(sh) - 2):
            left_idx, head_idx, right_idx = sh[i], sh[i + 1], sh[i + 2]
            head_h = h[head_idx]
            left_h = h[left_idx]
            right_h = h[right_idx]
            if head_h > left_h and head_h > right_h and _near(left_h, right_h, tolerance_pct * 2):
                patterns.append(ChartPattern("head_shoulders", "bearish", float(head_h), right_idx))

    # Inverse H&S: 3 swing low - ortadaki (bas) en dusuk
    if len(sl) >= 3:
        for i in range(len(sl) - 2):
            left_idx, head_idx, right_idx = sl[i], sl[i + 1], sl[i + 2]
            head_l = l[head_idx]
            left_l = l[left_idx]
            right_l = l[right_idx]
            if head_l < left_l and head_l < right_l and _near(left_l, right_l, tolerance_pct * 2):
                patterns.append(ChartPattern("inverse_head_shoulders", "bullish", float(head_l), right_idx))

    return patterns


# ---------------------------------------------------------------------------
# Turtle strategy (Bucek: ATR-based 20/55 bar breakout)
# ---------------------------------------------------------------------------

def detect_turtle_breakout(
    df: pd.DataFrame,
    atr_series: pd.Series | None = None,
    n_fast: int = 20,
    n_slow: int = 55,
) -> Literal["LONG", "SHORT", ""]:
    """Bucek: 20/55 bar high/low breakout. Fiyat N bar yuksek/dusugunu kirarsa sinyal."""
    if len(df) < n_fast + 2:
        return ""
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    i = len(df) - 1

    # Onceki N barin high/low (mevcut bar haric)
    high_20 = float(np.max(h[i - n_fast : i]))
    low_20 = float(np.min(l[i - n_fast : i]))

    if c[i] > high_20 and c[i - 1] <= high_20:
        return "LONG"
    if c[i] < low_20 and c[i - 1] >= low_20:
        return "SHORT"
    return ""


# ---------------------------------------------------------------------------
# Trend structure  (higher highs / lower lows)
# ---------------------------------------------------------------------------

def detect_trend(
    df: pd.DataFrame,
    lookback: int = 20,
    data_with_indicators: Optional[pd.DataFrame] = None,
) -> Literal["up", "down", "sideways"]:
    """Coklu teknik analiz ile trend: fiyat yapisi, EMA, RSI, MACD."""
    if len(df) < lookback:
        return "sideways"

    recent = df.iloc[-lookback:]
    highs = recent["high"].values
    lows = recent["low"].values
    closes = recent["close"].values

    # 1. Fiyat yapisi (higher highs / lower lows)
    higher_highs = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    lower_lows = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])
    hh_ratio = higher_highs / max(len(highs) - 1, 1)
    ll_ratio = lower_lows / max(len(lows) - 1, 1)

    # 2. Fiyat egilimi (son mumlarin egimi)
    first_half = np.mean(closes[: lookback // 2])
    second_half = np.mean(closes[lookback // 2 :])
    slope_pct = (second_half - first_half) / first_half * 100 if first_half > 0 else 0

    # 3. EMA / RSI / MACD (varsa)
    up_score, down_score = 0, 0
    if hh_ratio > 0.5:
        up_score += 2
    elif hh_ratio < 0.4:
        down_score += 1
    if ll_ratio > 0.5:
        down_score += 2
    elif ll_ratio < 0.4:
        up_score += 1
    if slope_pct > 0.5:
        up_score += 2
    elif slope_pct < -0.5:
        down_score += 2

    if data_with_indicators is not None and len(data_with_indicators) >= lookback:
        last = data_with_indicators.iloc[-1]
        close = float(last["close"])
        ema50 = float(last["ema_50"]) if "ema_50" in last and pd.notna(last["ema_50"]) else close
        ema200 = float(last["ema_200"]) if "ema_200" in last and pd.notna(last["ema_200"]) else close
        rsi = float(last["rsi"]) if "rsi" in last and pd.notna(last["rsi"]) else 50.0
        macd_h = float(last["macd_hist"]) if "macd_hist" in last and pd.notna(last["macd_hist"]) else 0.0

        if close > ema50 > ema200:
            up_score += 2
        elif close < ema50 < ema200:
            down_score += 2
        elif close > ema50:
            up_score += 1
        elif close < ema50:
            down_score += 1
        if rsi < 30:
            up_score += 1  # Oversold (Zanni)
        elif rsi > 70:
            down_score += 1  # Overbought (Zanni)
        elif rsi > 52:
            up_score += 1
        elif rsi < 48:
            down_score += 1
        if macd_h > 0:
            up_score += 1
        elif macd_h < 0:
            down_score += 1

    if up_score > down_score + 1:
        return "up"
    if down_score > up_score + 1:
        return "down"
    return "sideways"


# ---------------------------------------------------------------------------
# Scalp filtreleri
# ---------------------------------------------------------------------------

def candle_body_ratio(df: pd.DataFrame, idx: int) -> float:
    """Son mumda govde/range orani. 0.6+ = guclu yon, 0.3- = belirsiz."""
    if idx < 0 or idx >= len(df):
        return 0.5
    row = df.iloc[idx]
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    rng = h - l
    if rng <= 0:
        return 0.5
    body = abs(c - o)
    return body / rng


def consecutive_same_direction(df: pd.DataFrame, idx: int, lookback: int = 3) -> tuple[int, str | None]:
    """
    Son N mumda ayni yonde kac mum var.
    Returns: (count, "bullish"|"bearish"|None)
    """
    if idx < lookback:
        return 0, None
    o = df["open"].values
    c = df["close"].values
    count_bull = 0
    count_bear = 0
    for i in range(idx - lookback + 1, idx + 1):
        if c[i] > o[i]:
            count_bull += 1
        elif c[i] < o[i]:
            count_bear += 1
    if count_bull >= lookback - 1 and count_bull >= count_bear:
        return count_bull, "bullish"
    if count_bear >= lookback - 1 and count_bear >= count_bull:
        return count_bear, "bearish"
    return 0, None


def volume_spike_ratio(volume: float, vol_avg: float, min_ratio: float = 1.2) -> bool:
    """Hacim ortalama ustunde mi? Scalp icin min 1.2x gerekli."""
    if vol_avg <= 0:
        return True
    return volume >= vol_avg * min_ratio


def is_london_ny_session(utc_hour: int) -> bool:
    """London/NY oturumu: UTC 14:00-22:00 = yuksek likidite."""
    return 14 <= utc_hour < 22
