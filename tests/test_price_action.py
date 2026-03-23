"""price_action.py birim testleri."""
import numpy as np
import pandas as pd
import pytest

from price_action import (
    detect_swing_lows,
    detect_swing_highs,
    compute_pivot_points,
    nearest_support_resistance,
    Level,
    detect_patterns,
    compute_fibonacci_levels,
    check_volume_confirmation,
)


@pytest.fixture
def sample_df():
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


def test_pivot_points_returns_dict(sample_df):
    """Pivot noktalari dict donmeli."""
    p = compute_pivot_points(sample_df)
    assert p is not None
    assert "pivot" in p
    assert "r1" in p
    assert "s1" in p


def test_pivot_points_short_df():
    """2 mumdan azda None."""
    df = pd.DataFrame({"open": [100], "high": [101], "low": [99], "close": [100.5]})
    assert compute_pivot_points(df) is None


def test_nearest_support_resistance():
    levels = [
        Level(95, "support"),
        Level(100, "support"),
        Level(105, "resistance"),
        Level(110, "resistance"),
    ]
    sup, res = nearest_support_resistance(levels, 102)
    assert sup is not None and sup.price == 100
    assert res is not None and res.price == 105


def test_detect_patterns_returns_list(sample_df):
    """Pattern listesi donmeli."""
    pats = detect_patterns(sample_df)
    assert isinstance(pats, list)


def test_check_volume_confirmation():
    df = pd.DataFrame({"volume": [100, 200, 150]})
    ok, ratio = check_volume_confirmation(df, 1, 100.0, 1.2)
    assert ratio == 2.0
    assert ok is True
