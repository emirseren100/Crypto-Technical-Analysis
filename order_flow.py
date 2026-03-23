"""
Order Flow - CVD (Cumulative Volume Delta)
taker_buy_base = agresif alim hacmi, volume - taker_buy_base = satim hacmi
Delta = Alim - Satim. CVD = kumulatif delta.
"""
from typing import Optional

import numpy as np
import pandas as pd


def volume_delta(df: pd.DataFrame) -> pd.Series:
    """
    Mum basina Volume Delta: Alim hacmi - Satim hacmi.
    taker_buy_base = agresif alim (market buy), geri kalan = satim.
    """
    if "taker_buy_base" not in df.columns or "volume" not in df.columns:
        return pd.Series(0.0, index=df.index)
    buy = df["taker_buy_base"].astype(float)
    vol = df["volume"].astype(float)
    sell = vol - buy
    return buy - sell


def cvd(df: pd.DataFrame) -> pd.Series:
    """Cumulative Volume Delta - order flow trend."""
    delta = volume_delta(df)
    return delta.cumsum()


def cvd_ema(cvd_series: pd.Series, period: int = 20) -> pd.Series:
    """CVD'nin EMA'si - trend yonu."""
    return cvd_series.ewm(span=period, adjust=False).mean()


def cvd_bullish(cvd: pd.Series, cvd_ema: pd.Series, idx: int) -> bool:
    """CVD, CVD_EMA ustunde = alim baskisi (bullish)."""
    if idx < 0 or idx >= len(cvd) or pd.isna(cvd.iloc[idx]) or pd.isna(cvd_ema.iloc[idx]):
        return False
    return float(cvd.iloc[idx]) > float(cvd_ema.iloc[idx])


def delta_ratio_last_n(df: pd.DataFrame, n: int = 5) -> float:
    """
    Son N mumda toplam delta / toplam volume.
    > 0.1 = alim baskisi, < -0.1 = satim baskisi.
    """
    if len(df) < n or "taker_buy_base" not in df.columns:
        return 0.0
    tail = df.tail(n)
    buy = tail["taker_buy_base"].astype(float).sum()
    vol = tail["volume"].astype(float).sum()
    if vol <= 0:
        return 0.0
    sell = vol - buy
    return (buy - sell) / vol
