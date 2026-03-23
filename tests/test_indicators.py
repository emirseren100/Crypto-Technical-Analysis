"""indicators.py birim testleri."""
import numpy as np
import pandas as pd
import pytest

from indicators import ema, rsi, macd, atr, sma


@pytest.fixture
def sample_ohlcv():
    np.random.seed(42)
    n = 100
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n))
    low = close - np.abs(np.random.randn(n))
    return pd.DataFrame({
        "open": close - 0.2,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.rand(n) * 1e6,
    })


def test_rsi_range(sample_ohlcv):
    """RSI 0-100 araliginda olmali."""
    r = rsi(sample_ohlcv["close"], 14)
    valid = r.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_ema_length(sample_ohlcv):
    """EMA giris ile ayni uzunlukta."""
    c = sample_ohlcv["close"]
    e = ema(c, 20)
    assert len(e) == len(c)


def test_sma_length(sample_ohlcv):
    """SMA giris ile ayni uzunlukta."""
    c = sample_ohlcv["close"]
    s = sma(c, 20)
    assert len(s) == len(c)


def test_macd_returns_three_series(sample_ohlcv):
    """MACD 3 seri donmeli."""
    macd_line, sig, hist = macd(sample_ohlcv["close"])
    assert len(macd_line) == len(sample_ohlcv)
    assert len(sig) == len(sample_ohlcv)
    assert len(hist) == len(sample_ohlcv)


def test_atr_positive(sample_ohlcv):
    """ATR pozitif olmali."""
    a = atr(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"], 14)
    valid = a.dropna()
    assert (valid >= 0).all()
