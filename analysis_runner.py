"""
Ortak analiz calistirici - ana uygulama bu modulu kullanir.
Tum veriler (OB, OI, liquidations, Fear&Greed, funding hist, BTC korelasyonu vb.) cekilir.
"""
from typing import Optional

import pandas as pd

from data_fetcher import (
    fetch_exchange_flow_signal,
    fetch_fear_greed,
    fetch_funding_rate,
    fetch_funding_rate_history,
    fetch_liquidations,
    fetch_open_interest,
    fetch_order_book_imbalance,
    fetch_prev_day_high_low,
    fetch_ticker_24h,
    safe_fetch_klines,
)
from economic_calendar import get_economic_calendar_warning
from multi_timeframe import run_mtf_analysis
from signal_engine import AnalysisResult, analyze
from signal_history import get_calibration_stats


def run_full_analysis(
    symbol: str,
    interval: str = "30m",
    df: Optional[pd.DataFrame] = None,
    limit: int = 250,
    mode: str = "short",
    min_confidence: Optional[int] = None,
    use_symbol_calibration: bool = True,
    prev_direction: Optional[str] = None,
    analyses_in_direction: int = 0,
    relax_adx: bool = True,
    tp_profile: str = "normal",
) -> tuple[AnalysisResult, pd.DataFrame]:
    """
    Ana uygulama ile AYNI analiz - tum veriler cekilir.
    Returns: (AnalysisResult, df)
    """
    if df is None or df.empty:
        df = safe_fetch_klines(symbol, interval, limit)
    if df.empty or len(df) < 50:
        return (AnalysisResult(summary="Yetersiz veri"), df)

    if min_confidence is None and use_symbol_calibration:
        try:
            calib = get_calibration_stats(symbol, min_evaluated=5, mode=mode)
            min_confidence = calib["calibrated_min"] if calib["total"] >= 5 else 3
        except Exception:
            min_confidence = 3
    if min_confidence is None:
        min_confidence = 3

    mtf_consensus = None
    try:
        mtf = run_mtf_analysis(symbol, limit=150)
        if mtf.consensus in ("LONG", "SHORT"):
            mtf_consensus = mtf.consensus
    except Exception:
        pass

    funding_rate = None
    try:
        funding_rate = fetch_funding_rate(symbol)
    except Exception:
        pass

    liquidity_warning = False
    try:
        ticker = fetch_ticker_24h(symbol)
        btc_ticker = fetch_ticker_24h("BTCUSDT")
        if ticker and btc_ticker and btc_ticker.get("quoteVolume", 0) > 0:
            if ticker.get("quoteVolume", 0) / btc_ticker["quoteVolume"] < 0.01:
                liquidity_warning = True
    except Exception:
        pass

    ob_data = None
    try:
        ob_data = fetch_order_book_imbalance(symbol, 20)
    except Exception:
        pass
    ob_imb = ob_data["imbalance"] if ob_data else None
    spread_bps = ob_data["spread_bps"] if ob_data else None

    funding_hist = None
    oi = None
    prev_hl = None
    try:
        funding_hist = fetch_funding_rate_history(symbol, 24)
    except Exception:
        pass
    try:
        oi = fetch_open_interest(symbol)
    except Exception:
        pass
    try:
        prev_hl = fetch_prev_day_high_low(symbol)
    except Exception:
        pass

    econ_warn = get_economic_calendar_warning()
    fng = fetch_fear_greed()
    fear_greed_index = fng["value"] if fng else None
    liq = fetch_liquidations(symbol)
    flow = fetch_exchange_flow_signal()

    res = analyze(
        df,
        mtf_consensus=mtf_consensus,
        funding_rate=funding_rate,
        min_confidence=min_confidence,
        liquidity_warning=liquidity_warning,
        prev_direction=prev_direction,
        mode=mode,
        order_book_imbalance=ob_imb,
        spread_bps=spread_bps,
        symbol=symbol,
        interval=interval,
        funding_history=funding_hist,
        open_interest=oi,
        prev_day_hl=prev_hl,
        economic_warning=econ_warn,
        analyses_in_current_direction=analyses_in_direction,
        fear_greed_index=fear_greed_index,
        liquidations_24h=liq,
        exchange_flow_signal=flow,
        relax_adx=relax_adx,
        tp_profile=tp_profile,
    )
    return res, df
