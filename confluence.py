"""
Confluence Matrisi - 10 Kriterli Puanlama Sistemi
Puan > 6 ise isleme gir.
"""
from dataclasses import dataclass, field
from typing import Literal, Optional

import pandas as pd

from correlation import CorrelationResult, analyze_correlation
from market_structure import MarketStructureResult, detect_market_structure
from smc import (
    FairValueGap,
    OrderBlock,
    detect_fair_value_gaps,
    detect_order_blocks,
    fvg_filled_recently,
    price_near_fvg,
    price_near_order_block,
)


@dataclass
class ConfluenceScore:
    total: int
    max_possible: int = 10
    criteria: list[tuple[str, bool, str]] = field(default_factory=list)
    long_score: int = 0
    short_score: int = 0
    passed: bool = False
    min_to_enter: int = 6


def compute_confluence(
    df: pd.DataFrame,
    data: pd.DataFrame,
    close: float,
    support,
    resistance,
    proximity_pct: float,
    vol: float,
    vol_avg: float,
    rsi_divergence,  # Divergence | None
    mtf_consensus: Optional[str],
    order_book_imbalance: Optional[float],
    pattern_at: dict,
    last: int,
    direction: Literal["LONG", "SHORT"],
    symbol: str,
    interval: str,
    scalp: bool = False,
) -> ConfluenceScore:
    """
    10 kriter:
    1. Fiyat destek (LONG) veya direnc (SHORT) bolgesinde
    2. RSI divergence uyumlu
    3. Hacim artiyor
    4. FVG veya Order Block aktif
    5. Market Structure BOS uyumlu
    6. BTC trend uyumlu
    7. Dominance riskli degil
    8. MTF consensus uyumlu
    9. Order book imbalance uyumlu
    10. Mum formasyonu (pattern) destekliyor
    """
    criteria: list[tuple[str, bool, str]] = []
    score = 0

    near_support = support and abs(close - support.price) / close * 100 < proximity_pct
    near_resistance = resistance and abs(close - resistance.price) / close * 100 < proximity_pct

    if direction == "LONG":
        c1 = near_support
        criteria.append(("Destek bolgesinde", c1, "Fiyat destek yakininda" if c1 else "Destek uzak"))
    else:
        c1 = near_resistance
        criteria.append(("Direnc bolgesinde", c1, "Fiyat direnc yakininda" if c1 else "Direnc uzak"))
    if c1:
        score += 1

    c2 = False
    if rsi_divergence:
        c2 = (direction == "LONG" and rsi_divergence.kind == "bullish") or (
            direction == "SHORT" and rsi_divergence.kind == "bearish"
        )
    criteria.append(("RSI divergence uyumlu", c2, "Divergence onay" if c2 else "Divergence yok/uyumsuz"))
    if c2:
        score += 1

    c3 = vol > vol_avg * 1.2
    criteria.append(("Hacim artiyor", c3, f"Hacim {vol/vol_avg:.1f}x" if vol_avg > 0 else "Hacim ok"))
    if c3:
        score += 1

    obs = detect_order_blocks(df, lookback=30)
    fvgs = detect_fair_value_gaps(df, lookback=30)
    near_ob, _ = price_near_order_block(close, obs, 0.5)
    near_fvg, _ = price_near_fvg(close, fvgs, 0.5)
    fvg_filled = fvg_filled_recently(fvgs, "bullish" if direction == "LONG" else "bearish")
    c4 = near_ob or near_fvg or fvg_filled
    criteria.append(("FVG/Order Block aktif", c4, "SMC bolgesi" if c4 else "SMC yok"))
    if c4:
        score += 1

    ms = detect_market_structure(df, lookback=50, scalp=scalp)
    c5 = False
    if direction == "LONG":
        c5 = ms.trend == "bullish" or ms.last_bos == "bullish"
    else:
        c5 = ms.trend == "bearish" or ms.last_bos == "bearish"
    criteria.append(("Market Structure uyumlu", c5, f"MS: {ms.trend} BOS:{ms.last_bos}" if c5 else "MS uyumsuz"))
    if c5:
        score += 1

    corr = analyze_correlation(symbol, direction, interval, scalp)
    c6 = (direction == "LONG" and corr.btc_trend in ("up", "sideways")) or (
        direction == "SHORT" and corr.btc_trend in ("down", "sideways")
    )
    criteria.append(("BTC trend uyumlu", c6, f"BTC: {corr.btc_trend}" if c6 else corr.reason))
    if c6:
        score += 1

    c7 = not (corr.alt_long_risky and direction == "LONG") and not (corr.alt_short_risky and direction == "SHORT")
    criteria.append(("Dominance/Korelasyon OK", c7, "Risk yok" if c7 else corr.reason))
    if c7:
        score += 1

    c8 = False
    if mtf_consensus:
        c8 = (direction == "LONG" and mtf_consensus == "LONG") or (direction == "SHORT" and mtf_consensus == "SHORT")
    else:
        c8 = True
    criteria.append(("MTF consensus uyumlu", c8, f"MTF: {mtf_consensus or '--'}" if c8 else "MTF ters"))
    if c8:
        score += 1

    c9 = False
    if order_book_imbalance is not None:
        c9 = (direction == "LONG" and order_book_imbalance > 0.05) or (
            direction == "SHORT" and order_book_imbalance < -0.05
        )
    else:
        c9 = True
    criteria.append(("Order book uyumlu", c9, f"OB: {order_book_imbalance:+.2f}" if order_book_imbalance is not None else "OB --"))
    if c9:
        score += 1

    pat = pattern_at.get(last) or pattern_at.get(last - 1)
    c10 = False
    if pat:
        c10 = (direction == "LONG" and pat.direction == "bullish") or (
            direction == "SHORT" and pat.direction == "bearish"
        )
    criteria.append(("Mum formasyonu destekliyor", c10, pat.name if pat else "Formasyon yok"))
    if c10:
        score += 1

    min_to_enter = 6
    passed = score >= min_to_enter

    return ConfluenceScore(
        total=score,
        criteria=criteria,
        long_score=score if direction == "LONG" else 0,
        short_score=score if direction == "SHORT" else 0,
        passed=passed,
        min_to_enter=min_to_enter,
    )
