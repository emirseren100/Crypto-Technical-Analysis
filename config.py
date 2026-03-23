"""
Uygulama yapilandirmasi - tek yerden ayar.
Degisiklik icin kodu degistirmeden config guncellenebilir.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    min_confidence: int = 6
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    mtf_mandatory: bool = False
    use_extended_levels: bool = True
    volatility_warning_pct: float = 4.0
    volume_confirmation_min_ratio: float = 1.0
    backtest_tp1_ratio: float = 1.0 / 3
    backtest_tp2_ratio: float = 1.0 / 3
    backtest_tp3_ratio: float = 1.0 / 3


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
