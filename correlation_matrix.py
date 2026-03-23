"""
Korelasyon Matrisi - BTC, ETH, SOL vb. arasi fiyat korelasyonu
Ayni anda korele paritelerde pozisyon = risk artisi
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from data_fetcher import safe_fetch_klines


@dataclass
class CorrelationMatrixResult:
    symbols: list[str]
    matrix: dict[tuple[str, str], float]
    correlated_pairs: list[tuple[str, str, float]]
    warning: Optional[str] = None


def compute_correlation_matrix(
    symbols: list[str],
    interval: str = "1h",
    limit: int = 100,
) -> CorrelationMatrixResult:
    """
    Pariteler arasi getiri korelasyonu (Pearson).
    Korelasyon > 0.7 = yuksek, ayni anda pozisyon riskli.
    """
    if len(symbols) < 2:
        return CorrelationMatrixResult(symbols, {}, [], None)

    closes: dict[str, pd.Series] = {}
    for sym in symbols[:8]:
        try:
            df = safe_fetch_klines(sym, interval, limit)
            if len(df) >= 30:
                ret = df["close"].pct_change().dropna()
                closes[sym] = ret
        except Exception:
            continue

    if len(closes) < 2:
        return CorrelationMatrixResult(list(closes.keys()), {}, [], None)

    aligned = pd.DataFrame(closes).dropna()
    if len(aligned) < 20:
        return CorrelationMatrixResult(list(closes.keys()), {}, [], None)

    corr_df = aligned.corr()
    matrix: dict[tuple[str, str], float] = {}
    correlated: list[tuple[str, str, float]] = []

    for i, s1 in enumerate(corr_df.columns):
        for j, s2 in enumerate(corr_df.columns):
            if i >= j:
                continue
            c = float(corr_df.loc[s1, s2])
            matrix[(s1, s2)] = c
            if c > 0.7:
                correlated.append((s1, s2, c))

    warning = None
    if correlated:
        pairs = ", ".join(f"{a}/{b}({c:.2f})" for a, b, c in correlated[:5])
        warning = f"Yuksek korelasyon: {pairs}. Ayni anda pozisyon acmayin."

    return CorrelationMatrixResult(
        symbols=list(closes.keys()),
        matrix=matrix,
        correlated_pairs=correlated,
        warning=warning,
    )
