"""
Market Structure (MS) - HHL, LLH, BOS
Higher High (HH), Higher Low (HL), Lower High (LH), Lower Low (LL)
Break of Structure (BOS) - trend degisimi veya devam
"""
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import pandas as pd


@dataclass
class SwingPoint:
    idx: int
    price: float
    kind: Literal["high", "low"]


@dataclass
class MarketStructureResult:
    trend: Literal["bullish", "bearish", "ranging"]
    last_bos: Literal["bullish", "bearish", ""]
    last_bos_idx: int
    structure: str
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    fakeout_warning: bool = False
    choch: Literal["bullish", "bearish", ""] = ""  # Change of Character


def _swing_window(df: pd.DataFrame, scalp: bool) -> int:
    return 2 if scalp else 3


def _get_swing_highs(df: pd.DataFrame, lookback: int, swing_window: int) -> list[SwingPoint]:
    h = df["high"].values
    n = len(df)
    start = max(0, n - lookback)
    points: list[SwingPoint] = []
    for i in range(start + swing_window, n - swing_window):
        ok = True
        for j in range(1, swing_window + 1):
            if h[i] <= h[i - j] or h[i] <= h[i + j]:
                ok = False
                break
        if ok:
            points.append(SwingPoint(i, float(h[i]), "high"))
    return points


def _get_swing_lows(df: pd.DataFrame, lookback: int, swing_window: int) -> list[SwingPoint]:
    l = df["low"].values
    n = len(df)
    start = max(0, n - lookback)
    points: list[SwingPoint] = []
    for i in range(start + swing_window, n - swing_window):
        ok = True
        for j in range(1, swing_window + 1):
            if l[i] >= l[i - j] or l[i] >= l[i + j]:
                ok = False
                break
        if ok:
            points.append(SwingPoint(i, float(l[i]), "low"))
    return points


def detect_market_structure(
    df: pd.DataFrame,
    lookback: int = 50,
    scalp: bool = False,
) -> MarketStructureResult:
    """
    HH/HL = yukselis yapisi. LH/LL = dusus yapisi.
    BOS (Break of Structure): Fiyat son swing high'ı yukarı kırdı = bullish BOS.
    """
    if len(df) < 20:
        return MarketStructureResult("ranging", "", -1, "", [], [], False, "")

    swing_window = _swing_window(df, scalp)
    swing_highs = _get_swing_highs(df, lookback, swing_window)
    swing_lows = _get_swing_lows(df, lookback, swing_window)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return MarketStructureResult(
            "ranging", "", -1, "", swing_highs, swing_lows, False, ""
        )

    sh = swing_highs[-4:]
    sl = swing_lows[-4:]

    structure_parts: list[str] = []
    last_bos = ""
    last_bos_idx = -1
    trend = "ranging"
    fakeout = False

    for i in range(1, len(sh)):
        if sh[i].price > sh[i - 1].price:
            structure_parts.append("HH")
        else:
            structure_parts.append("LH")
    for i in range(1, len(sl)):
        if sl[i].price > sl[i - 1].price:
            structure_parts.append("HL")
        else:
            structure_parts.append("LL")

    hh_count = structure_parts.count("HH")
    hl_count = structure_parts.count("HL")
    lh_count = structure_parts.count("LH")
    ll_count = structure_parts.count("LL")

    if hh_count >= 1 and hl_count >= 1:
        trend = "bullish"
    elif lh_count >= 1 and ll_count >= 1:
        trend = "bearish"

    last_swing_high = sh[-1] if sh else None
    last_swing_low = sl[-1] if sl else None
    for i in range(len(df) - 1, max(0, len(df) - 30), -1):
        high_i = float(df["high"].iloc[i])
        low_i = float(df["low"].iloc[i])
        if last_swing_high and high_i > last_swing_high.price and i > last_swing_high.idx:
            last_bos = "bullish"
            last_bos_idx = i
            break
        if last_swing_low and low_i < last_swing_low.price and i > last_swing_low.idx:
            last_bos = "bearish"
            last_bos_idx = i
            break

    structure_str = " ".join(structure_parts[-4:]) if structure_parts else ""

    choch = ""
    if last_bos == "bullish" and len(sl) >= 2:
        if sl[-1].price > sl[-2].price:
            choch = "bullish"
    elif last_bos == "bearish" and len(sh) >= 2:
        if sh[-1].price < sh[-2].price:
            choch = "bearish"

    return MarketStructureResult(
        trend=trend,
        last_bos=last_bos,
        last_bos_idx=last_bos_idx,
        structure=structure_str,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        fakeout_warning=fakeout,
        choch=choch,
    )
