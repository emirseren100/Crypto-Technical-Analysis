import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# RSI  (Relative Strength Index)
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# MACD  (Moving Average Convergence Divergence)
# ---------------------------------------------------------------------------

def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(close, period)
    rolling_std = close.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std
    return upper, middle, lower


# ---------------------------------------------------------------------------
# ATR  (Average True Range)
# ---------------------------------------------------------------------------

def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Volume Moving Average
# ---------------------------------------------------------------------------

def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return sma(volume, period)


# ---------------------------------------------------------------------------
# OBV (On-Balance Volume)
# ---------------------------------------------------------------------------

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    direction = np.where(close > prev_close, 1, np.where(close < prev_close, -1, 0))
    return pd.Series((volume.values * direction).cumsum(), index=close.index)


# ---------------------------------------------------------------------------
# ADX (Average Directional Index) - trend strength
# ---------------------------------------------------------------------------

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns +DI, -DI, ADX. ADX > 25 = strong trend, ADX < 20 = ranging."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_val = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_val)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return plus_di, minus_di, adx_val


# ---------------------------------------------------------------------------
# Bollinger %B and Bandwidth
# ---------------------------------------------------------------------------

def bb_percent_b(close: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """%B: 0 = lower band, 1 = upper band. >1 overbought, <0 oversold."""
    width = upper - lower
    result = (close - lower) / width.replace(0, np.nan)
    return result.fillna(0.5).clip(-1, 2)


def stochastic_rsi(rsi: pd.Series, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI: 0-100. %K ve %D."""
    rsi_min = rsi.rolling(period, min_periods=period).min()
    rsi_max = rsi.rolling(period, min_periods=period).max()
    stoch = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    stoch = stoch.fillna(0.5) * 100
    k = stoch.rolling(smooth_k, min_periods=1).mean()
    d = k.rolling(smooth_d, min_periods=1).mean()
    return k, d


def bb_bandwidth(upper: pd.Series, middle: pd.Series, lower: pd.Series) -> pd.Series:
    """Bandwidth: (upper - lower) / middle * 100. Low = squeeze."""
    return (upper - lower) / middle.replace(0, np.nan) * 100


# ---------------------------------------------------------------------------
# VWAP (Volume Weighted Average Price)
# ---------------------------------------------------------------------------

def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """VWAP = cumsum(typical_price * volume) / cumsum(volume). Typical = (H+L+C)/3."""
    typical = (high + low + close) / 3
    return (typical * volume).cumsum() / volume.cumsum().replace(0, np.nan)


# ---------------------------------------------------------------------------
# Convenience: compute all indicators at once
# ---------------------------------------------------------------------------

def compute_all(df: pd.DataFrame, scalp: bool = False) -> pd.DataFrame:
    """Scalp modunda RSI 7, ATR 7, MACD 9/19/7 kullanilir."""
    out = df.copy()

    rsi_per = 7 if scalp else 14
    atr_per = 7 if scalp else 14
    adx_per = 7 if scalp else 14

    out["sma_9"] = sma(df["close"], 9)
    out["sma_21"] = sma(df["close"], 21)
    out["ema_50"] = ema(df["close"], 50)
    out["ema_200"] = ema(df["close"], 200)

    out["rsi"] = rsi(df["close"], rsi_per)
    out["stoch_rsi_k"], out["stoch_rsi_d"] = stochastic_rsi(out["rsi"], rsi_per, 3, 3)

    if scalp:
        out["macd"], out["macd_signal"], out["macd_hist"] = macd(
            df["close"], fast=9, slow=19, signal_period=7
        )
    else:
        out["macd"], out["macd_signal"], out["macd_hist"] = macd(df["close"])

    out["bb_upper"], out["bb_middle"], out["bb_lower"] = bollinger_bands(df["close"])

    out["atr"] = atr(df["high"], df["low"], df["close"], atr_per)

    out["vol_sma"] = volume_sma(df["volume"], 20)
    out["obv"] = obv(df["close"], df["volume"])
    out["obv_ema"] = ema(out["obv"], 20)

    out["plus_di"], out["minus_di"], out["adx"] = adx(
        df["high"], df["low"], df["close"], adx_per
    )

    out["bb_pct_b"] = bb_percent_b(df["close"], out["bb_upper"], out["bb_lower"])
    out["bb_bandwidth"] = bb_bandwidth(out["bb_upper"], out["bb_middle"], out["bb_lower"])
    out["vwap"] = vwap(df["high"], df["low"], df["close"], df["volume"])

    try:
        from order_flow import cvd, volume_delta
        out["volume_delta"] = volume_delta(df)
        out["cvd"] = cvd(df)
        out["cvd_ema"] = out["cvd"].ewm(span=20, adjust=False).mean()
    except Exception:
        out["volume_delta"] = pd.Series(0.0, index=df.index)
        out["cvd"] = pd.Series(0.0, index=df.index)
        out["cvd_ema"] = pd.Series(0.0, index=df.index)

    vw = out["vwap"]
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap_std = typical.rolling(20).std()
    out["vwap_upper_1std"] = vw + vwap_std
    out["vwap_lower_1std"] = vw - vwap_std

    return out
