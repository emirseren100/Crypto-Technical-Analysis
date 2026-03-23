"""
Smart Money Concepts (SMC)
- Order Blocks: Son guclu mum onceki hareketin baslangici
- Fair Value Gap (FVG): 3 mumluk bosluk - likidite bolgesi
"""
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd


@dataclass
class OrderBlock:
    idx: int
    direction: Literal["bullish", "bearish"]
    high: float
    low: float
    price: float  # entry zone
    strength: float  # 0-1


@dataclass
class FairValueGap:
    idx: int
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    filled: bool
    fill_pct: float  # 0-1 ne kadar dolduruldu


def detect_order_blocks(
    df: pd.DataFrame,
    lookback: int = 30,
    min_body_ratio: float = 0.5,
) -> list[OrderBlock]:
    """
    Bullish OB: Son yesil mum onceki dususun basinda - alim bolgesi.
    Bearish OB: Son kirmizi mum onceki yukselisin basinda - satim bolgesi.
    """
    if len(df) < 10:
        return []
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    start = max(0, n - lookback)
    obs: list[OrderBlock] = []

    for i in range(start + 2, n - 2):
        body = abs(c[i] - o[i])
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        body_ratio = body / rng
        if body_ratio < min_body_ratio:
            continue

        if c[i] < o[i]:
            next_high = max(h[i + 1], h[i + 2]) if i + 2 < n else h[i + 1]
            move_up = (next_high - l[i]) / l[i] * 100 if l[i] > 0 else 0
            if move_up > 0.5:
                obs.append(OrderBlock(
                    idx=i, direction="bullish",
                    high=float(h[i]), low=float(l[i]),
                    price=float(l[i]),
                    strength=min(1.0, body_ratio * 1.5),
                ))
        else:
            next_low = min(l[i + 1], l[i + 2]) if i + 2 < n else l[i + 1]
            move_down = (h[i] - next_low) / h[i] * 100 if h[i] > 0 else 0
            if move_down > 0.5:
                obs.append(OrderBlock(
                    idx=i, direction="bearish",
                    high=float(h[i]), low=float(l[i]),
                    price=float(h[i]),
                    strength=min(1.0, body_ratio * 1.5),
                ))
    return obs[-5:]


def detect_fair_value_gaps(
    df: pd.DataFrame,
    lookback: int = 30,
    min_gap_pct: float = 0.05,
) -> list[FairValueGap]:
    """
    Bullish FVG: Mum1 high < Mum3 low - aradaki bosluk.
    Bearish FVG: Mum1 low > Mum3 high - aradaki bosluk.
    filled: Fiyat boslugu doldurdu mu?
    """
    if len(df) < 10:
        return []
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    start = max(0, n - lookback)
    fvgs: list[FairValueGap] = []

    for i in range(start + 2, n):
        mid = (c[i - 1] + o[i - 1]) / 2
        if mid <= 0:
            continue

        if h[i - 2] < l[i]:
            gap = l[i] - h[i - 2]
            if gap / mid * 100 >= min_gap_pct:
                top = float(l[i])
                bottom = float(h[i - 2])
                filled = any(l[j] <= top for j in range(i, min(i + 20, n)))
                fill_pct = 0.0
                if filled:
                    for j in range(i, min(i + 20, n)):
                        if l[j] <= bottom:
                            fill_pct = 1.0
                            break
                        elif l[j] <= top:
                            fill_pct = (top - l[j]) / (top - bottom) if top > bottom else 1.0
                            break
                fvgs.append(FairValueGap(i, "bullish", top, bottom, filled, fill_pct))

        if l[i - 2] > h[i]:
            gap = l[i - 2] - h[i]
            if gap / mid * 100 >= min_gap_pct:
                top = float(l[i - 2])
                bottom = float(h[i])
                filled = any(h[j] >= bottom for j in range(i, min(i + 20, n)))
                fill_pct = 0.0
                if filled:
                    for j in range(i, min(i + 20, n)):
                        if h[j] >= top:
                            fill_pct = 1.0
                            break
                        elif h[j] >= bottom:
                            fill_pct = (h[j] - bottom) / (top - bottom) if top > bottom else 1.0
                            break
                fvgs.append(FairValueGap(i, "bearish", top, bottom, filled, fill_pct))

    return fvgs[-5:]


def count_order_blocks_at_zone(
    obs: list[OrderBlock],
    price: float,
    direction: str,
    pct: float = 0.5,
) -> int:
    """
    Ayni fiyat bolgesinde kac OB var. 2+ = yigilma, guclu seviye.
    """
    if not obs:
        return 0
    zone_pct = pct / 100
    count = 0
    for ob in obs:
        if ob.direction != direction:
            continue
        if abs(ob.price - price) / price <= zone_pct:
            count += 1
    return count


def price_near_order_block(close: float, obs: list[OrderBlock], pct: float = 0.5) -> tuple[bool, Optional[OrderBlock]]:
    """Fiyat order block yakininda mi?"""
    for ob in obs:
        if ob.direction == "bullish":
            if close <= ob.high and close >= ob.low * 0.998:
                return True, ob
            if abs(close - ob.price) / close * 100 < pct:
                return True, ob
        else:
            if close >= ob.low and close <= ob.high * 1.002:
                return True, ob
            if abs(close - ob.price) / close * 100 < pct:
                return True, ob
    return False, None


def price_near_fvg(close: float, fvgs: list[FairValueGap], pct: float = 0.3) -> tuple[bool, Optional[FairValueGap]]:
    """Fiyat FVG bolgesinde mi ve doldurulmamis mi?"""
    for fvg in fvgs:
        if fvg.filled and fvg.fill_pct > 0.8:
            continue
        if fvg.direction == "bullish":
            if fvg.bottom <= close <= fvg.top:
                return True, fvg
            if abs(close - (fvg.top + fvg.bottom) / 2) / close * 100 < pct:
                return True, fvg
        else:
            if fvg.bottom <= close <= fvg.top:
                return True, fvg
            if abs(close - (fvg.top + fvg.bottom) / 2) / close * 100 < pct:
                return True, fvg
    return False, None


def fvg_filled_recently(fvgs: list[FairValueGap], direction: Literal["bullish", "bearish"]) -> bool:
    """Son FVG dolduruldu mu - reversal onayı."""
    for fvg in reversed(fvgs):
        if fvg.direction != direction:
            continue
        if fvg.filled and fvg.fill_pct > 0.5:
            return True
    return False


# ---------------------------------------------------------------------------
# Likidite Havuzlari (Liquidity Pools) - Equal Highs / Equal Lows
# Stop hunt bolgeleri - balinalar stop loss'lari toplar
# ---------------------------------------------------------------------------

@dataclass
class LiquidityPool:
    price: float
    kind: Literal["equal_highs", "equal_lows"]
    count: int
    idxs: list[int]


def detect_liquidity_pools(
    df: pd.DataFrame,
    lookback: int = 30,
    tolerance_pct: float = 0.15,
) -> list[LiquidityPool]:
    """
    Equal Highs: Birden fazla swing high yakin seviyede = ustte likidite, short stop'lari.
    Equal Lows: Birden fazla swing low yakin seviyede = altta likidite, long stop'lari.
    """
    if len(df) < 10:
        return []
    h = df["high"].values
    l = df["low"].values
    n = len(df)
    start = max(0, n - lookback)
    pools: list[LiquidityPool] = []

    def _near(a: float, b: float, pct: float) -> bool:
        if a <= 0:
            return abs(b - a) < pct
        return abs(a - b) / a * 100 < pct

    swing_highs = [
        (i, float(h[i]))
        for i in range(start + 2, n - 2)
        if h[i] >= h[i - 1] and h[i] >= h[i - 2] and h[i] >= h[i + 1] and h[i] >= h[i + 2]
    ]
    swing_lows = [
        (i, float(l[i]))
        for i in range(start + 2, n - 2)
        if l[i] <= l[i - 1] and l[i] <= l[i - 2] and l[i] <= l[i + 1] and l[i] <= l[i + 2]
    ]

    def _cluster(points: list[tuple[int, float]], kind: str) -> list[LiquidityPool]:
        result: list[LiquidityPool] = []
        used: set[int] = set()
        for i, (idx1, p1) in enumerate(points):
            if idx1 in used:
                continue
            group_idx = [idx1]
            group_prices = [p1]
            for j, (idx2, p2) in enumerate(points):
                if j <= i or idx2 in used:
                    continue
                if _near(p1, p2, tolerance_pct):
                    group_idx.append(idx2)
                    group_prices.append(p2)
                    used.add(idx2)
            if len(group_idx) >= 2:
                used.add(idx1)
                result.append(LiquidityPool(
                    sum(group_prices) / len(group_prices), kind, len(group_idx), group_idx
                ))
        return result

    pools = _cluster(swing_highs, "equal_highs") + _cluster(swing_lows, "equal_lows")
    return pools[-6:]


def price_near_liquidity_pool(close: float, pools: list[LiquidityPool], pct: float = 0.3) -> tuple[bool, Optional[LiquidityPool]]:
    """Fiyat likidite havuzuna yakin mi? Stop hunt riski."""
    for pool in pools:
        if abs(close - pool.price) / close * 100 < pct:
            return True, pool
    return False, None
