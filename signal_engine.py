from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import pandas as pd

from indicators import compute_all
from confluence import compute_confluence
from tp_profiles import get_tp_multipliers, normalize_tp_profile
from price_action import (
    CandlePattern,
    Level,
    candle_body_ratio,
    check_volume_confirmation,
    compute_fibonacci_levels,
    compute_pivot_points,
    compute_volume_profile,
    consecutive_same_direction,
    detect_chart_patterns,
    detect_liquidity_grab,
    detect_macd_divergence,
    detect_obv_divergence,
    detect_patterns,
    detect_rsi_divergence,
    detect_structure_break,
    detect_trend,
    detect_turtle_breakout,
    find_support_resistance,
    find_support_resistance_extended,
    is_london_ny_session,
    nearest_support_resistance,
    volume_spike_ratio,
)


@dataclass
class TradeSetup:
    time: datetime
    direction: Literal["LONG", "SHORT"]
    confidence: int                     # 1-10
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    rr1: float                          # risk:reward for TP1
    rr2: float
    rr3: float
    risk_pct: float                     # SL distance as % of entry
    reasons: list[str] = field(default_factory=list)
    limit_entry: float = 0.0            # Limit emir seviyesi (pullback giris)
    setup_type: str = ""                # hammer, fvg, ob, divergence, turtle, chart_pattern
    entry_zone_low: float = 0.0         # Giris bolgesi alt sinir
    entry_zone_high: float = 0.0        # Giris bolgesi ust sinir
    tp_priority: str = ""               # Dinamik R:R: tp1/tp2/tp3 onceligi


@dataclass
class AnalysisResult:
    setup: Optional[TradeSetup] = None
    summary: str = "Setup yok -- kosullar uygun degil"
    indicators: dict = field(default_factory=dict)
    wait_for_long: Optional[float] = None
    wait_for_short: Optional[float] = None


def _format_summary(direction: str, entry: float, reasons: list[str]) -> str:
    """Kesin sonuc formati: YON | Fiyat/Seviye | Neden"""
    neden = ", ".join(reasons[:3]) if reasons else "Skor bazli"
    return f"{direction} | Entry: {entry:.2f} | Neden: {neden}"

def analyze(
    df: pd.DataFrame,
    proximity_pct: float = 1.5,
    mtf_consensus: Optional[Literal["LONG", "SHORT", "BEKLE"]] = None,
    funding_rate: Optional[float] = None,
    min_confidence: int = 6,
    liquidity_warning: bool = False,
    relax_adx: bool = False,
    prev_direction: Optional[Literal["LONG", "SHORT"]] = None,
    mode: Literal["short", "long", "scalp"] = "short",
    direction_flip_hysteresis: int = 4,
    order_book_imbalance: Optional[float] = None,
    spread_bps: Optional[float] = None,
    symbol: str = "",
    interval: str = "1h",
    funding_history: Optional[list] = None,
    open_interest: Optional[float] = None,
    prev_day_hl: Optional[dict] = None,
    economic_warning: Optional[str] = None,
    analyses_in_current_direction: int = 0,
    fear_greed_index: Optional[int] = None,
    liquidations_24h: Optional[dict] = None,
    exchange_flow_signal: Optional[Literal["inflow", "outflow"]] = None,
    mtf_mandatory: Optional[bool] = None,
    use_extended_levels: Optional[bool] = None,
    tp_profile: str = "normal",
) -> AnalysisResult:
    if len(df) < 50:
        return AnalysisResult(summary="Yetersiz veri (min 50 mum)")

    try:
        from config import get_config
        cfg = get_config()
        _mtf_mandatory = mtf_mandatory if mtf_mandatory is not None else cfg.mtf_mandatory
        _use_extended = use_extended_levels if use_extended_levels is not None else cfg.use_extended_levels
    except ImportError:
        _mtf_mandatory = mtf_mandatory if mtf_mandatory is not None else False
        _use_extended = use_extended_levels if use_extended_levels is not None else True

    if _mtf_mandatory and mtf_consensus == "BEKLE":
        return AnalysisResult(
            summary="MTF zorunlu: Ust zaman dilimi BEKLE diyor - islem acma.",
            indicators={"mtf_consensus": mtf_consensus, "mtf_mandatory_blocked": True},
        )

    scalp_mode = mode == "scalp"
    tp_prof = normalize_tp_profile(tp_profile)
    tp_m1, tp_m2, tp_m3 = get_tp_multipliers(tp_profile, scalp_mode)
    # Scalp: cok sert flip filtreleri - kararlar oynamasin
    _hyst = 8 if scalp_mode else direction_flip_hysteresis
    _score_gap_min = 4 if scalp_mode else 2
    _min_analyses_before_flip = 4 if scalp_mode else 0  # scalp: en az 4 analiz ayni yonde, sonra flip
    data = compute_all(df, scalp=scalp_mode)
    levels = find_support_resistance_extended(df) if _use_extended else find_support_resistance(df)
    patterns = detect_patterns(df)
    trend = detect_trend(df, data_with_indicators=data)
    pattern_at = {p.index: p for p in patterns}
    last = len(data) - 1
    close = float(data["close"].iloc[last])
    high_last = float(data["high"].iloc[last])
    low_last = float(data["low"].iloc[last])

    rsi_val = _safe(data["rsi"].iloc[last], 50.0)
    macd_h = _safe(data["macd_hist"].iloc[last], 0.0)
    macd_h_prev = _safe(data["macd_hist"].iloc[last - 1], 0.0)
    macd_line = _safe(data["macd"].iloc[last], 0.0)
    macd_sig = _safe(data["macd_signal"].iloc[last], 0.0)
    atr_val = _safe(data["atr"].iloc[last], 0.0)
    bb_upper = _safe(data["bb_upper"].iloc[last], close + 1)
    bb_lower = _safe(data["bb_lower"].iloc[last], close - 1)
    ema50 = _safe(data["ema_50"].iloc[last], close)
    ema200 = _safe(data["ema_200"].iloc[last], close)
    sma9 = _safe(data["sma_9"].iloc[last], close)
    sma21 = _safe(data["sma_21"].iloc[last], close)
    vol = float(data["volume"].iloc[last])
    vol_avg = _safe(data["vol_sma"].iloc[last], vol)
    adx_val = _safe(data["adx"].iloc[last], 25.0)
    obv_val = _safe(data["obv"].iloc[last], 0.0)
    obv_ema_val = _safe(data["obv_ema"].iloc[last], obv_val)
    bb_pct_b = _safe(data["bb_pct_b"].iloc[last], 0.5)
    stoch_k = _safe(data["stoch_rsi_k"].iloc[last], 50.0)
    open_last = float(data["open"].iloc[last])
    vwap_val = _safe(data["vwap"].iloc[last], None) if "vwap" in data.columns else None

    fib_levels = compute_fibonacci_levels(df, lookback=50)
    vp = compute_volume_profile(df, lookback=100)
    structure = detect_structure_break(df, lookback=50, scalp=scalp_mode)

    pivots = compute_pivot_points(df)
    if pivots:
        for key, kind in [("s1", "support"), ("s2", "support"), ("r1", "resistance"), ("r2", "resistance")]:
            if key in pivots:
                levels.append(Level(price=pivots[key], kind=kind, touches=2))
        p = pivots.get("pivot")
        if p is not None:
            levels.append(Level(price=p, kind="support" if p < close else "resistance", touches=2))
        levels = sorted(levels, key=lambda lv: lv.price)
    support, resistance = nearest_support_resistance(levels, close)

    volatility_pct = (atr_val / close * 100) if close > 0 else 0.0
    prox_pct = proximity_pct
    near_support = support and abs(close - support.price) / close * 100 < prox_pct
    near_resistance = resistance and abs(close - resistance.price) / close * 100 < prox_pct
    indicators = {
        "mtf_consensus": mtf_consensus,
        "close": close,
        "rsi": rsi_val,
        "macd_hist": macd_h,
        "macd_line": macd_line,
        "macd_signal": macd_sig,
        "atr": atr_val,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "ema50": ema50,
        "sma21": sma21,
        "trend": trend,
        "support": support.price if support else None,
        "resistance": resistance.price if resistance else None,
        "volume": vol,
        "vol_avg": vol_avg,
        "volatility_pct": volatility_pct,
        "funding_rate": funding_rate,
        "liquidity_warning": liquidity_warning,
        "adx": adx_val,
        "bb_pct_b": bb_pct_b,
        "stoch_rsi_k": stoch_k,
        "pivots": pivots,
        "obv_trend_bullish": obv_val > obv_ema_val if obv_ema_val else False,
        "near_support": near_support,
        "near_resistance": near_resistance,
        "fib_levels": fib_levels,
        "volume_profile": vp,
        "structure_break": structure,
        "tp_profile": tp_prof,
    }

    if atr_val <= 0:
        return AnalysisResult(summary="ATR hesaplanamadi", indicators=indicators)

    wait_long = support.price if support else (close - atr_val * 1.2)
    wait_short = resistance.price if resistance else (close + atr_val * 1.2)

    price_above_vwap = (close > vwap_val) if vwap_val is not None else None

    # --- Score long/short ---
    long_score, long_reasons = _score_long(
        close, rsi_val, macd_h, macd_h_prev, macd_line, macd_sig,
        bb_lower, ema50, sma21, trend, support, resistance,
        vol, vol_avg, pattern_at, last, proximity_pct,
        obv_trend_bullish=obv_val > obv_ema_val if obv_ema_val else False,
        bb_pct_b=bb_pct_b,
        stoch_rsi_k=stoch_k,
        sma9=sma9,
        ema200=ema200,
        open_last=open_last,
        price_above_vwap=price_above_vwap,
    )
    short_score, short_reasons = _score_short(
        close, rsi_val, macd_h, macd_h_prev, macd_line, macd_sig,
        bb_upper, ema50, sma21, trend, support, resistance,
        vol, vol_avg, pattern_at, last, proximity_pct,
        obv_trend_bullish=obv_val > obv_ema_val if obv_ema_val else False,
        bb_pct_b=bb_pct_b,
        stoch_rsi_k=stoch_k,
        sma9=sma9,
        ema200=ema200,
        open_last=open_last,
        price_above_vwap=price_above_vwap,
    )

    div = detect_rsi_divergence(df, data["rsi"], lookback=30)
    if div:
        if div.kind == "bullish":
            long_score += 2
            long_reasons.append("RSI bullish divergence (fiyat dusus, RSI yukselis)")
        else:
            short_score += 2
            short_reasons.append("RSI bearish divergence (fiyat yukselis, RSI dusus)")

    macd_div = detect_macd_divergence(df, data["macd_hist"], lookback=30)
    if macd_div:
        if macd_div.kind == "bullish":
            long_score += 1
            long_reasons.append("MACD bullish divergence")
        else:
            short_score += 1
            short_reasons.append("MACD bearish divergence")

    # PDF: Turtle breakout (Bucek) + Chart patterns (Galen Woods)
    turtle = detect_turtle_breakout(df)
    if turtle == "LONG":
        long_score += 2
        long_reasons.append("Turtle 20 bar breakout (yukari)")
    elif turtle == "SHORT":
        short_score += 2
        short_reasons.append("Turtle 20 bar breakout (asagi)")
    chart_pats = detect_chart_patterns(df)
    for cp in chart_pats:
        if cp.idx >= last - 5:
            if cp.direction == "bullish":
                long_score += 2
                long_reasons.append(f"Grafik formasyonu: {cp.name}")
            else:
                short_score += 2
                short_reasons.append(f"Grafik formasyonu: {cp.name}")

    vol_conf_ok, vol_ratio = check_volume_confirmation(df, last, vol_avg, 1.2 if scalp_mode else 1.0)
    if not vol_conf_ok and not scalp_mode:
        long_score = max(0, long_score - 1)
        short_score = max(0, short_score - 1)
        long_reasons.append(f"Hacim onayi zayif ({vol_ratio:.1f}x ortalama)")
        short_reasons.append(f"Hacim onayi zayif ({vol_ratio:.1f}x ortalama)")
    indicators["volume_confirmation"] = vol_conf_ok
    indicators["volume_ratio"] = vol_ratio

    cvd_bull = False
    cvd_bear = False
    delta_ratio_val = 0.0
    if "cvd" in data.columns and "cvd_ema" in data.columns:
        try:
            from order_flow import delta_ratio_last_n
            cvd_val = _safe(data["cvd"].iloc[last], 0.0)
            cvd_ema_val = _safe(data["cvd_ema"].iloc[last], 0.0)
            cvd_bull = cvd_val > cvd_ema_val
            cvd_bear = cvd_val < cvd_ema_val
            delta_ratio_val = delta_ratio_last_n(df, 5)
            indicators["cvd_bullish"] = cvd_bull
            indicators["delta_ratio_5"] = delta_ratio_val
        except Exception:
            pass
    if cvd_bull:
        long_score += 1
        long_reasons.append("CVD alim baskisi (order flow)")
    if cvd_bear:
        short_score += 1
        short_reasons.append("CVD satim baskisi (order flow)")
    if abs(delta_ratio_val) > 0.15:
        if delta_ratio_val > 0:
            long_score += 1
            long_reasons.append(f"Son 5 mum delta +%{delta_ratio_val*100:.0f} (agresif alim)")
        else:
            short_score += 1
            short_reasons.append(f"Son 5 mum delta %{delta_ratio_val*100:.0f} (agresif satim)")

    indicators["long_score"] = long_score
    indicators["short_score"] = short_score
    indicators["turtle"] = turtle
    indicators["divergence"] = div.kind if div else None
    indicators["macd_divergence"] = macd_div.kind if macd_div else None
    indicators["chart_patterns_bullish"] = sum(1 for cp in chart_pats if cp.idx >= last - 5 and cp.direction == "bullish")
    indicators["chart_patterns_bearish"] = sum(1 for cp in chart_pats if cp.idx >= last - 5 and cp.direction == "bearish")

    # Scalp filtreleri: mum yapisi, ardışık mum, hacim, oturum
    body_ratio = candle_body_ratio(df, last)
    consec_count, consec_dir = consecutive_same_direction(df, last, lookback=3)
    vol_ok = volume_spike_ratio(vol, vol_avg, 1.2 if scalp_mode else 1.0)
    utc_hour = datetime.utcnow().hour
    session_ok = is_london_ny_session(utc_hour)
    if session_ok and not scalp_mode:
        if long_score > short_score:
            long_score += 1
            long_reasons.append("London/NY seansi - hacim onay")
        elif short_score > long_score:
            short_score += 1
            short_reasons.append("London/NY seansi - hacim onay")

    if scalp_mode:
        if body_ratio >= 0.6:
            if close > open_last:
                long_score += 1
                long_reasons.append("Guclu govde (scalp onay)")
            else:
                short_score += 1
                short_reasons.append("Guclu govde (scalp onay)")
        if consec_dir == "bullish" and consec_count >= 2:
            long_score += 1
            long_reasons.append(f"Ardisik {consec_count} yukselis mumu")
        elif consec_dir == "bearish" and consec_count >= 2:
            short_score += 1
            short_reasons.append(f"Ardisik {consec_count} dusus mumu")
        if not vol_ok:
            long_score = max(0, long_score - 2)
            short_score = max(0, short_score - 2)
            long_reasons.append("Scalp: hacim yetersiz (min 1.2x ortalama)")
            short_reasons.append("Scalp: hacim yetersiz (min 1.2x ortalama)")
        if not session_ok:
            indicators["session_warning"] = f"UTC {utc_hour}:00 - London/NY disinda (14-22 UTC onerilir)"
            long_reasons.append("Scalp: London/NY disinda - likidite dusuk")
            short_reasons.append("Scalp: London/NY disinda - likidite dusuk")
            long_score = max(0, long_score - 1)
            short_score = max(0, short_score - 1)
        else:
            indicators["session_warning"] = None

    # Order book: bid/ask dengesi - alim baskisi LONG, satim baskisi SHORT
    if order_book_imbalance is not None:
        indicators["order_book_imbalance"] = order_book_imbalance
        if order_book_imbalance > 0.15:
            long_score += 1
            long_reasons.append(f"Order book: alim baskisi ({order_book_imbalance:.2f})")
        elif order_book_imbalance < -0.15:
            short_score += 1
            short_reasons.append(f"Order book: satim baskisi ({order_book_imbalance:.2f})")

    # Spread uyarisi: yuksek spread scalp icin risk
    if spread_bps is not None:
        indicators["spread_bps"] = spread_bps
        if scalp_mode and spread_bps > 15:
            long_reasons.append(f"UYARI: Yuksek spread {spread_bps:.1f} bps - scalp risk")
            short_reasons.append(f"UYARI: Yuksek spread {spread_bps:.1f} bps - scalp risk")
            long_score = max(0, long_score - 1)
            short_score = max(0, short_score - 1)
        elif spread_bps > 30:
            indicators["spread_warning"] = f"Spread {spread_bps:.1f} bps - dusuk likidite"

    from ta_enhancements import atr_percentile, detect_regime, obv_trend
    regime = detect_regime(adx_val)
    indicators["regime"] = regime.regime
    indicators["regime_strength"] = regime.strength
    obv_tr = obv_trend(obv_val, obv_ema_val)
    indicators["obv_trend"] = obv_tr

    # Volatilite rejimi: ATR yuzdelik dilimi - 90+ = asiri volatil, sinyal zayiflat
    atr_pct = atr_percentile(data["atr"], lookback=50)
    indicators["atr_percentile"] = atr_pct
    if atr_pct >= 90:
        long_reasons.append(f"ATR %{atr_pct:.0f} dilim - yuksek volatilite rejimi")
        short_reasons.append(f"ATR %{atr_pct:.0f} dilim - yuksek volatilite rejimi")
        long_score = max(0, long_score - 1)
        short_score = max(0, short_score - 1)

    # Funding asirilik: 0.1% (0.001) uzeri - guclu sinyal
    if funding_rate is not None:
        if funding_rate >= 0.001:
            short_score += 1
            short_reasons.append(f"Funding asirilik %{funding_rate*100:.2f} (long odeme)")
        elif funding_rate <= -0.001:
            long_score += 1
            long_reasons.append(f"Funding asirilik %{funding_rate*100:.2f} (short odeme)")

    # Likidite grab: destek/direnc altinda wick + geri donus
    liq_bull, liq_bear = detect_liquidity_grab(df, support, resistance, close, lookback=10)
    if liq_bull:
        long_score += 2
        long_reasons.append("Likidite grab (destek altinda wick + geri donus)")
    if liq_bear:
        short_score += 2
        short_reasons.append("Likidite grab (direnc ustunde wick + geri donus)")
    indicators["liquidity_grab"] = "bullish" if liq_bull else ("bearish" if liq_bear else None)

    # OBV divergence
    obv_div = detect_obv_divergence(df, data["obv"], lookback=30)
    if obv_div:
        if obv_div == "bullish":
            long_score += 2
            long_reasons.append("OBV bullish divergence (fiyat dusus, OBV yukselis)")
        else:
            short_score += 2
            short_reasons.append("OBV bearish divergence (fiyat yukselis, OBV dusus)")
    indicators["obv_divergence"] = obv_div

    # Order block yigilmasi
    from smc import count_order_blocks_at_zone, detect_order_blocks
    obs = detect_order_blocks(df, lookback=30)
    ob_count_long = count_order_blocks_at_zone(obs, close, "bullish", 0.5)
    ob_count_short = count_order_blocks_at_zone(obs, close, "bearish", 0.5)
    indicators["ob_stack_long"] = ob_count_long
    indicators["ob_stack_short"] = ob_count_short
    if ob_count_long >= 2:
        long_score += 1
        long_reasons.append(f"Order block yigilmasi ({ob_count_long}x bullish OB)")
    if ob_count_short >= 2:
        short_score += 1
        short_reasons.append(f"Order block yigilmasi ({ob_count_short}x bearish OB)")

    # ADX'e gore min_confidence: ADX dusuk = ranging, daha yuksek guven gerek
    if adx_val < 20:
        min_confidence = max(min_confidence, 7)
        indicators["adx_low_min_conf_raised"] = True
    elif adx_val >= 40:
        min_confidence = max(1, min_confidence - 1)
        indicators["adx_strong_min_conf_relaxed"] = True

    # Seans bazli skor: mevcut seans win rate yuksekse +1 (tum semboller uzerinden)
    try:
        from signal_history import get_session_win_rates
        sess = get_session_win_rates(symbol=None, min_trades=5)
        cur = sess.get("current", "")
        cur_data = sess.get(cur, {})
        if cur_data.get("win_rate", 0) >= 55 and cur_data.get("trades", 0) >= 5:
            long_score += 1
            short_score += 1
            long_reasons.append(f"Seans {cur} win rate %{cur_data['win_rate']}")
            short_reasons.append(f"Seans {cur} win rate %{cur_data['win_rate']}")
        indicators["session_win_rate"] = cur_data
    except Exception:
        pass

    min_threshold = 5 if mode == "long" else (5 if scalp_mode else 3)
    if regime.regime == "ranging":
        min_threshold += 1

    conf_long = compute_confluence(
        df, data, close, support, resistance, prox_pct,
        vol, vol_avg, div, mtf_consensus, order_book_imbalance,
        pattern_at, last, "LONG", symbol, interval, scalp_mode,
    )
    conf_short = compute_confluence(
        df, data, close, support, resistance, prox_pct,
        vol, vol_avg, div, mtf_consensus, order_book_imbalance,
        pattern_at, last, "SHORT", symbol, interval, scalp_mode,
    )
    indicators["confluence_long"] = conf_long.total
    indicators["confluence_short"] = conf_short.total
    indicators["confluence_long_passed"] = conf_long.passed
    indicators["confluence_short_passed"] = conf_short.passed
    indicators["confluence_criteria"] = conf_long.criteria

    from market_structure import detect_market_structure
    ms = detect_market_structure(df, lookback=50, scalp=scalp_mode)
    indicators["market_structure"] = ms.trend
    indicators["market_structure_bos"] = ms.last_bos
    indicators["market_structure_choch"] = ms.choch
    indicators["market_structure_str"] = ms.structure
    # MTF yapi uyumu: market structure HH/HL vs LH/LL
    if ms.trend == "bullish" and mtf_consensus == "LONG":
        long_score += 1
        long_reasons.append("MTF yapi uyumu (HH/HL + ust TF LONG)")
    elif ms.trend == "bearish" and mtf_consensus == "SHORT":
        short_score += 1
        short_reasons.append("MTF yapi uyumu (LH/LL + ust TF SHORT)")
    indicators["mtf_structure_aligned"] = (ms.trend == "bullish" and mtf_consensus == "LONG") or (ms.trend == "bearish" and mtf_consensus == "SHORT")

    from smc import detect_liquidity_pools, price_near_liquidity_pool
    liquidity_pools = detect_liquidity_pools(df, lookback=30)
    near_liq, liq_pool = price_near_liquidity_pool(close, liquidity_pools, 0.4)
    indicators["liquidity_pool_near"] = near_liq
    indicators["liquidity_pools"] = [(p.kind, p.price, p.count) for p in liquidity_pools]

    if funding_history:
        indicators["funding_history"] = funding_history
        if len(funding_history) >= 3:
            recent = [x["rate"] for x in funding_history[-6:]]
            indicators["funding_trend"] = "yuksek" if sum(recent) > 0.0003 else ("dusuk" if sum(recent) < -0.0003 else "nötr")
    if open_interest is not None:
        indicators["open_interest"] = open_interest
    if prev_day_hl:
        indicators["prev_day_high"] = prev_day_hl.get("high")
        indicators["prev_day_low"] = prev_day_hl.get("low")
    if vwap_val is not None:
        indicators["vwap"] = vwap_val
        indicators["price_vs_vwap"] = "ustunde" if close > vwap_val else "altinda"
    if economic_warning:
        indicators["economic_warning"] = economic_warning
        long_reasons.append(economic_warning)
        short_reasons.append(economic_warning)

    # Fear & Greed: asiri korkuda long agirligi, asiri acgozlulukte short agirligi
    if fear_greed_index is not None:
        indicators["fear_greed_index"] = fear_greed_index
        if fear_greed_index < 25:
            long_score += 1
            long_reasons.append(f"Fear & Greed {fear_greed_index} (asiri korku - long bias)")
        elif fear_greed_index > 75:
            short_score += 1
            short_reasons.append(f"Fear & Greed {fear_greed_index} (asiri acgozluluk - short bias)")

    # Funding: skora guclu etki - yuksek funding LONG icin puan dusur, SHORT icin artir (nedenler build_setup'ta)
    if funding_rate is not None:
        if funding_rate > 0.0001:
            long_score = max(0, long_score - 1)
            short_score += 1
        elif funding_rate < -0.0001:
            long_score += 1
            short_score = max(0, short_score - 1)

    # Volatilite uyarisi: ATR %4+ - gosterge ve nedenlere ekle (guven build_setup'ta zaten dusuyor)
    if volatility_pct > 4.0:
        indicators["volatility_warning"] = True
        long_reasons.append(f"Yuksek volatilite %{volatility_pct:.1f} - dikkatli ol")
        short_reasons.append(f"Yuksek volatilite %{volatility_pct:.1f} - dikkatli ol")
    else:
        indicators["volatility_warning"] = False

    # Liquidasyon verisi: Long liq >> Short liq = long squeeze, SHORT bias. Short liq >> Long liq = short squeeze, LONG bias.
    if liquidations_24h:
        indicators["liquidations_24h"] = liquidations_24h
        long_liq = liquidations_24h.get("long_liq_usd", 0) or 0
        short_liq = liquidations_24h.get("short_liq_usd", 0) or 0
        total_liq = long_liq + short_liq
        if total_liq > 100_000:
            if long_liq > short_liq * 2:
                short_score += 1
                short_reasons.append("Long likidasyon baskisi (squeeze)")
            elif short_liq > long_liq * 2:
                long_score += 1
                long_reasons.append("Short likidasyon baskisi (squeeze)")

    # On-chain / exchange flow (varsa skora hafif etki)
    if exchange_flow_signal:
        indicators["exchange_flow"] = exchange_flow_signal
        if exchange_flow_signal == "outflow":
            long_score += 1
            long_reasons.append("Borsa cikisi (whale) - long bias")
        elif exchange_flow_signal == "inflow":
            short_score += 1
            short_reasons.append("Borsa girisi (whale) - short bias")

    # Ensemble: basit indikatör modeli (RSI, MACD, trend, S/R) - sadece iki model uyumluysa sinyal
    long_b, short_b = 0, 0
    if rsi_val < 45:
        long_b += 1
    elif rsi_val > 55:
        short_b += 1
    if macd_line > macd_sig:
        long_b += 1
    elif macd_line < macd_sig:
        short_b += 1
    if trend == "up":
        long_b += 1
    elif trend == "down":
        short_b += 1
    if near_support:
        long_b += 1
    if near_resistance:
        short_b += 1
    indicators["ensemble_long_b"] = long_b
    indicators["ensemble_short_b"] = short_b

    from session import analyze_session
    session_res = analyze_session()
    indicators["session_weekend"] = session_res.is_weekend
    indicators["session_ny_london"] = session_res.is_ny_london_open
    indicators["session_warning"] = session_res.session_warning
    indicators["session_sl_widen"] = session_res.sl_widen_suggested
    if session_res.session_warning:
        long_reasons.append(session_res.session_warning)
        short_reasons.append(session_res.session_warning)
        if session_res.is_weekend:
            long_score = max(0, long_score - 1)
            short_score = max(0, short_score - 1)

    def _would_flip_to_long() -> bool:
        return long_score >= min_threshold and long_score >= short_score
    def _would_flip_to_short() -> bool:
        return short_score >= min_threshold and short_score >= long_score

    def _hysteresis_blocks_flip(to_direction: Literal["LONG", "SHORT"]) -> bool:
        """Yon degisiminde histerezis: kucuk fiyat hareketinde flip onle. Yeni yon en az N puan onde olmali."""
        if not prev_direction or prev_direction == to_direction:
            return False
        if to_direction == "LONG":
            return long_score < short_score + _hyst
        return short_score < long_score + _hyst

    def _score_gap_insufficient() -> bool:
        """Skorlar cok yakin mi? Yeterli fark yoksa flip yapma (scalp'ta daha sert)."""
        gap = abs(long_score - short_score)
        return gap < _score_gap_min

    def _mtf_blocks_flip(to_direction: Literal["LONG", "SHORT"]) -> bool:
        """Ust zaman dilimi mevcut yonu destekliyorsa flip'i zorlastir - tek mumla donme."""
        if not prev_direction or not mtf_consensus or mtf_consensus == "BEKLE":
            return False
        if prev_direction == "LONG" and mtf_consensus == "LONG" and to_direction == "SHORT":
            return True  # MTF LONG diyor, SHORT'a donme
        if prev_direction == "SHORT" and mtf_consensus == "SHORT" and to_direction == "LONG":
            return True  # MTF SHORT diyor, LONG'a donme
        return False

    def _flip_lock_active() -> bool:
        """Yeterince analiz yapilmadi - flip kilidi: onceki yonde kal."""
        if _min_analyses_before_flip <= 0 or not prev_direction:
            return False
        return analyses_in_current_direction < _min_analyses_before_flip

    def _mtf_opposite_gap_ok(to_direction: Literal["LONG", "SHORT"]) -> bool:
        """MTF ters yondeyse tahmin icin daha buyuk skor farki gerekir - yanlis tahmini azalt."""
        if not mtf_consensus or mtf_consensus == "BEKLE":
            return True
        if to_direction == "LONG" and mtf_consensus == "SHORT":
            return long_score >= short_score + 3
        if to_direction == "SHORT" and mtf_consensus == "LONG":
            return short_score >= long_score + 3
        return True

    def _ensemble_ok(to_direction: Literal["LONG", "SHORT"]) -> bool:
        """Iki model uyumlu mu: PA + basit indikatör modeli ayni yonde."""
        if to_direction == "LONG":
            return long_b >= short_b
        return short_b >= long_b

    sl_widen = session_res.sl_widen_suggested

    # Belirsiz: skorlar cok yakin - zoraki yon verme, BEKLE
    gap = abs(long_score - short_score)
    if prev_direction is None and gap <= 1:
        indicators["wait_reason"] = "skorlar_yakin"
        return AnalysisResult(
            setup=None,
            summary=f"Belirsiz (LONG {long_score} / SHORT {short_score}). Skorlar cok yakin - daha net sinyal bekleyin.",
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    # Ranging'de ilk tahmin icin en az 2 puan fark gerek
    if prev_direction is None and regime.regime == "ranging" and gap < 2:
        indicators["wait_reason"] = "ranging_yetersiz_fark"
        return AnalysisResult(
            setup=None,
            summary=f"Ranging piyasa - yon icin yeterli fark yok (gap={gap}). Bekleyin.",
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    # Flip kilidi: scalp'ta en az N analiz ayni yonde olmadan flip yasak
    if prev_direction and _flip_lock_active():
        if prev_direction == "LONG":
            setup = _build_long_setup(
                data, last, close, atr_val, support, resistance,
                levels, long_score, short_score, long_reasons,
                funding_rate=funding_rate,
                volatility_pct=volatility_pct,
                liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("LONG", setup.entry, long_reasons + [f"(flip kilidi: {analyses_in_current_direction}/{_min_analyses_before_flip})"]),
                indicators=indicators,
                wait_for_long=wait_long,
                wait_for_short=wait_short,
            )
        setup = _build_short_setup(
            data, last, close, atr_val, support, resistance,
            levels, short_score, long_score, short_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("SHORT", setup.entry, short_reasons + [f"(flip kilidi: {analyses_in_current_direction}/{_min_analyses_before_flip})"]),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    # Uygun yonde LONG veya SHORT ver - BEKLE yerine en guclu sinyali sec
    # MTF ters yondeyse daha buyuk skor farki; confluence gecmezse dusuk guvende setup verme; ensemble uyum
    min_conf_req = session_res.min_confluence_suggested
    if _would_flip_to_long() and not _hysteresis_blocks_flip("LONG") and not (prev_direction == "SHORT" and _score_gap_insufficient()) and not _mtf_blocks_flip("LONG") and _mtf_opposite_gap_ok("LONG") and _ensemble_ok("LONG"):
        setup = _build_long_setup(
            data, last, close, atr_val, support, resistance,
            levels, long_score, short_score, long_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        passed = conf_long.passed and conf_long.total >= min_conf_req
        if not passed:
            setup.confidence = max(1, setup.confidence - 2)
            long_reasons.append(f"Confluence yetersiz ({conf_long.total}/10, min {min_conf_req})")
        if long_b >= 3 and short_b <= 1:
            setup.confidence = min(10, setup.confidence + 1)
            long_reasons.append("Ensemble guclu uyum (PA + indikatör)")
        if not passed and setup.confidence < min_confidence:
            indicators["confluence_criteria"] = conf_long.criteria
            return AnalysisResult(
                setup=None,
                summary=f"LONG sinyali zayif (Confluence {conf_long.total}/10, guven {setup.confidence}<{min_confidence}). Bekleyin.",
                indicators=indicators,
                wait_for_long=wait_long,
                wait_for_short=wait_short,
            )
        indicators["confluence_criteria"] = conf_long.criteria
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("LONG", setup.entry, long_reasons),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    if _would_flip_to_short() and not _hysteresis_blocks_flip("SHORT") and not (prev_direction == "LONG" and _score_gap_insufficient()) and not _mtf_blocks_flip("SHORT") and _mtf_opposite_gap_ok("SHORT") and _ensemble_ok("SHORT"):
        setup = _build_short_setup(
            data, last, close, atr_val, support, resistance,
            levels, short_score, long_score, short_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        passed = conf_short.passed and conf_short.total >= min_conf_req
        if not passed:
            setup.confidence = max(1, setup.confidence - 2)
            short_reasons.append(f"Confluence yetersiz ({conf_short.total}/10, min {min_conf_req})")
        if short_b >= 3 and long_b <= 1:
            setup.confidence = min(10, setup.confidence + 1)
            short_reasons.append("Ensemble guclu uyum (PA + indikatör)")
        if not passed and setup.confidence < min_confidence:
            indicators["confluence_criteria"] = conf_short.criteria
            return AnalysisResult(
                setup=None,
                summary=f"SHORT sinyali zayif (Confluence {conf_short.total}/10, guven {setup.confidence}<{min_confidence}). Bekleyin.",
                indicators=indicators,
                wait_for_long=wait_long,
                wait_for_short=wait_short,
            )
        indicators["confluence_criteria"] = conf_short.criteria
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("SHORT", setup.entry, short_reasons),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    # Histerezis: yon degisimi engellendi, onceki yonde kal
    if prev_direction == "SHORT" and _would_flip_to_long() and _hysteresis_blocks_flip("LONG"):
        setup = _build_short_setup(
            data, last, close, atr_val, support, resistance,
            levels, short_score, long_score, short_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("SHORT", setup.entry, short_reasons + ["(yon degisimi filtrelendi)"]),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )
    if prev_direction == "LONG" and _would_flip_to_short() and _hysteresis_blocks_flip("SHORT"):
        setup = _build_long_setup(
            data, last, close, atr_val, support, resistance,
            levels, long_score, short_score, long_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("LONG", setup.entry, long_reasons + ["(yon degisimi filtrelendi)"]),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )

    # Skorlar esit veya dusuk: onceki yonde kal (flip yapma) - ufacik hareketle yon degismesin
    if prev_direction == "LONG":
        setup = _build_long_setup(
            data, last, close, atr_val, support, resistance,
            levels, long_score, short_score, long_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("LONG", setup.entry, long_reasons + ["(onceki yon korundu)"]),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )
    if prev_direction == "SHORT":
        setup = _build_short_setup(
            data, last, close, atr_val, support, resistance,
            levels, short_score, long_score, short_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("SHORT", setup.entry, short_reasons + ["(onceki yon korundu)"]),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )
    # prev_direction yok (ilk analiz): guclu olani sec; MTF tersse yine BEKLE; ensemble uyum
    if long_score >= short_score and _mtf_opposite_gap_ok("LONG") and _ensemble_ok("LONG"):
        setup = _build_long_setup(
            data, last, close, atr_val, support, resistance,
            levels, long_score, short_score, long_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        passed = conf_long.passed and conf_long.total >= min_conf_req
        if not passed:
            setup.confidence = max(1, setup.confidence - 2)
        if not passed and setup.confidence < min_confidence:
            return AnalysisResult(setup=None, summary="LONG icin confluence/guven yetersiz - bekleyin.", indicators=indicators, wait_for_long=wait_long, wait_for_short=wait_short)
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("LONG", setup.entry, long_reasons),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )
    if short_score >= long_score and _mtf_opposite_gap_ok("SHORT") and _ensemble_ok("SHORT"):
        setup = _build_short_setup(
            data, last, close, atr_val, support, resistance,
            levels, short_score, long_score, short_reasons,
            funding_rate=funding_rate,
            volatility_pct=volatility_pct,
            liquidity_warning=liquidity_warning,
            scalp=scalp_mode,
            sl_widen=sl_widen,
            regime=regime.regime,
            adx_val=adx_val,
            tp1_mult=tp_m1,
            tp2_mult=tp_m2,
            tp3_mult=tp_m3,
        )
        passed = conf_short.passed and conf_short.total >= min_conf_req
        if not passed:
            setup.confidence = max(1, setup.confidence - 2)
        if not passed and setup.confidence < min_confidence:
            return AnalysisResult(setup=None, summary="SHORT icin confluence/guven yetersiz - bekleyin.", indicators=indicators, wait_for_long=wait_long, wait_for_short=wait_short)
        return AnalysisResult(
            setup=setup,
            summary=_format_summary("SHORT", setup.entry, short_reasons),
            indicators=indicators,
            wait_for_long=wait_long,
            wait_for_short=wait_short,
        )
    # MTF, ensemble veya yetersiz fark - zoraki yon verme
    return AnalysisResult(
        setup=None,
        summary="Yon belirsiz (MTF, ensemble uyumsuzlugu veya skor farki yetersiz). Daha net sinyal bekleyin.",
        indicators=indicators,
        wait_for_long=wait_long,
        wait_for_short=wait_short,
    )


# ---------------------------------------------------------------------------
# Scoring (backtest icin - analiz ile ayni mantik)
# ---------------------------------------------------------------------------

def score_at_index(
    data: pd.DataFrame,
    df: pd.DataFrame,
    i: int,
    proximity_pct: float = 1.5,
    scalp: bool = False,
) -> tuple[int, int]:
    """Backtest icin: i. mumda long/short skoru. Analiz ile ayni algoritma."""
    from price_action import (
        detect_chart_patterns,
        detect_patterns,
        detect_trend,
        detect_turtle_breakout,
        find_support_resistance,
        nearest_support_resistance,
    )

    if i < 20:
        return 0, 0

    df_slice = df.iloc[: i + 1]
    levels = find_support_resistance(df_slice)
    patterns = detect_patterns(df_slice)
    pattern_at = {p.index: p for p in patterns}
    trend = detect_trend(df_slice)

    close = float(data["close"].iloc[i])
    rsi_val = _safe(data["rsi"].iloc[i], 50.0)
    macd_h = _safe(data["macd_hist"].iloc[i], 0.0)
    macd_h_prev = _safe(data["macd_hist"].iloc[i - 1], 0.0)
    macd_line = _safe(data["macd"].iloc[i], 0.0)
    macd_sig = _safe(data["macd_signal"].iloc[i], 0.0)
    bb_upper = _safe(data["bb_upper"].iloc[i], close + 1)
    bb_lower = _safe(data["bb_lower"].iloc[i], close - 1)
    ema50 = _safe(data["ema_50"].iloc[i], close)
    sma21 = _safe(data["sma_21"].iloc[i], close)
    sma9 = _safe(data["sma_9"].iloc[i], close)
    ema200 = _safe(data["ema_200"].iloc[i], close)
    vol = float(data["volume"].iloc[i])
    vol_avg = _safe(data["vol_sma"].iloc[i], vol)
    obv_val = _safe(data["obv"].iloc[i], 0.0)
    obv_ema = _safe(data["obv_ema"].iloc[i], obv_val)
    bb_pct_b = _safe(data["bb_pct_b"].iloc[i], 0.5)
    stoch_k = _safe(data["stoch_rsi_k"].iloc[i], 50.0)
    open_last = float(data["open"].iloc[i])

    support, resistance = nearest_support_resistance(levels, close)
    obv_bullish = obv_val > obv_ema if obv_ema else False

    vwap_i = _safe(data["vwap"].iloc[i], None) if "vwap" in data.columns else None
    price_above_vwap = (close > vwap_i) if vwap_i is not None else None

    long_score, _ = _score_long(
        close, rsi_val, macd_h, macd_h_prev, macd_line, macd_sig,
        bb_lower, ema50, sma21, trend, support, resistance,
        vol, vol_avg, pattern_at, i, proximity_pct,
        obv_trend_bullish=obv_bullish, bb_pct_b=bb_pct_b, stoch_rsi_k=stoch_k,
        sma9=sma9, ema200=ema200, open_last=open_last,
        price_above_vwap=price_above_vwap,
    )
    short_score, _ = _score_short(
        close, rsi_val, macd_h, macd_h_prev, macd_line, macd_sig,
        bb_upper, ema50, sma21, trend, support, resistance,
        vol, vol_avg, pattern_at, i, proximity_pct,
        obv_trend_bullish=obv_bullish, bb_pct_b=bb_pct_b, stoch_rsi_k=stoch_k,
        sma9=sma9, ema200=ema200, open_last=open_last,
        price_above_vwap=price_above_vwap,
    )

    if scalp:
        from price_action import candle_body_ratio, consecutive_same_direction, volume_spike_ratio
        if not volume_spike_ratio(vol, vol_avg, 1.2):
            long_score = max(0, long_score - 2)
            short_score = max(0, short_score - 2)
        body_ratio = candle_body_ratio(df, i)
        if body_ratio >= 0.6:
            if close > open_last:
                long_score += 1
            else:
                short_score += 1
        consec_count, consec_dir = consecutive_same_direction(df, i, lookback=3)
        if consec_dir == "bullish" and consec_count >= 2:
            long_score += 1
        elif consec_dir == "bearish" and consec_count >= 2:
            short_score += 1
    # PDF: Turtle + Chart patterns
    turtle = detect_turtle_breakout(df_slice)
    if turtle == "LONG":
        long_score += 2
    elif turtle == "SHORT":
        short_score += 2
    for cp in detect_chart_patterns(df_slice):
        if cp.idx >= i - 5:
            if cp.direction == "bullish":
                long_score += 2
            else:
                short_score += 2

    # --- B: Trend filtresi + ADX minimum ---
    adx_val = _safe(data["adx"].iloc[i], 25.0) if "adx" in data.columns else 25.0

    if adx_val < 15:
        long_score = max(0, long_score - 3)
        short_score = max(0, short_score - 3)
    elif adx_val < 20:
        long_score = max(0, long_score - 1)
        short_score = max(0, short_score - 1)

    if trend == "down" and close < ema200:
        long_score = max(0, long_score - 3)
        short_score += 1
    elif trend == "up" and close > ema200:
        short_score = max(0, short_score - 3)
        long_score += 1

    if abs(long_score - short_score) <= 1 and long_score > 0:
        long_score = max(0, long_score - 2)
        short_score = max(0, short_score - 2)

    return long_score, short_score


def _score_long(
    close, rsi, macd_h, macd_h_prev, macd_line, macd_sig,
    bb_lower, ema50, sma21, trend, support, resistance,
    vol, vol_avg, pattern_at, last, prox_pct,
    obv_trend_bullish: bool = False,
    bb_pct_b: float = 0.5,
    stoch_rsi_k: float = 50.0,
    sma9: float = 0.0,
    ema200: float = 0.0,
    open_last: float = 0.0,
    price_above_vwap: Optional[bool] = None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if stoch_rsi_k < 20:
        score += 1
        reasons.append("Stoch RSI oversold")
    elif stoch_rsi_k < 30:
        score += 1
        reasons.append("Stoch RSI dusuk")

    if rsi < 30:
        score += 2
        reasons.append(f"RSI asiri satim ({rsi:.1f}) [Zanni]")
    elif rsi < 45:
        score += 1
        reasons.append(f"RSI dusuk bolge ({rsi:.1f})")

    if support and abs(close - support.price) / close * 100 < prox_pct:
        score += 2
        if support.touches >= 3:
            score += 2
            reasons.append(f"Cok guclu destek ({support.price:.2f}, {support.touches}x dokunma)")
        elif support.touches >= 2:
            score += 1
            reasons.append(f"Guclu destek ({support.price:.2f}, {support.touches}x dokunma)")
        else:
            reasons.append(f"Destek yakininda ({support.price:.2f})")

    if macd_h > macd_h_prev and macd_h_prev < 0:
        score += 2
        reasons.append("MACD histogram yukari donuyor")
    elif macd_line > macd_sig:
        score += 1
        reasons.append("MACD sinyal ustunde")

    pat = pattern_at.get(last) or pattern_at.get(last - 1)
    if pat and pat.direction == "bullish":
        score += 2
        reasons.append(f"Yukselis formasyonu ({pat.name})")
        # PDF (Galen Woods): Bullish pattern AT support = guclu context
        if support and abs(close - support.price) / close * 100 < prox_pct:
            score += 1
            reasons.append("Formasyon destekte (Price Action context)")

    if close <= bb_lower:
        score += 1
        reasons.append("Fiyat Bollinger alt bandinda")

    if trend == "up":
        score += 1
        reasons.append("Genel trend yukselis")

    if close > ema50:
        score += 1
        reasons.append("Fiyat EMA50 ustunde")

    if vol > vol_avg * 1.3:
        score += 1
        reasons.append("Hacim ortalama ustu")

    # OBV trend onayi: hacim fiyatla uyumlu
    if obv_trend_bullish:
        score += 1
        reasons.append("OBV yukselis (hacim onayli)")

    # BB %B: alt banda yakin = iyi long firsati
    if bb_pct_b < 0:
        score += 1
        reasons.append("BB %B oversold")
    elif bb_pct_b < 0.3:
        score += 1
        reasons.append("BB %B dusuk bolge")

    # MA siralamasi: guclu yukselis trend
    if sma9 > 0 and ema200 > 0 and close > sma9 > sma21 > ema50 > ema200:
        score += 2
        reasons.append("MA siralamasi yukselis (guclu trend)")

    # Hacim yon onayi: yukselis mumu + yuksek hacim
    if open_last > 0 and close > open_last and vol > vol_avg * 1.2:
        score += 1
        reasons.append("Yukselis mumu + hacim onayi")

    # VWAP: fiyat VWAP ustunde = long momentum
    if price_above_vwap is True:
        score += 1
        reasons.append("Fiyat VWAP ustunde (momentum)")

    return score, reasons


def _score_short(
    close, rsi, macd_h, macd_h_prev, macd_line, macd_sig,
    bb_upper, ema50, sma21, trend, support, resistance,
    vol, vol_avg, pattern_at, last, prox_pct,
    obv_trend_bullish: bool = False,
    bb_pct_b: float = 0.5,
    stoch_rsi_k: float = 50.0,
    sma9: float = 0.0,
    ema200: float = 0.0,
    open_last: float = 0.0,
    price_above_vwap: Optional[bool] = None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if stoch_rsi_k > 80:
        score += 1
        reasons.append("Stoch RSI overbought")
    elif stoch_rsi_k > 70:
        score += 1
        reasons.append("Stoch RSI yuksek")

    if rsi > 70:
        score += 2
        reasons.append(f"RSI asiri alim ({rsi:.1f}) [Zanni]")
    elif rsi > 60:
        score += 1
        reasons.append(f"RSI yuksek bolge ({rsi:.1f})")

    if resistance and abs(close - resistance.price) / close * 100 < prox_pct:
        score += 2
        if resistance.touches >= 3:
            score += 2
            reasons.append(f"Cok guclu direnc ({resistance.price:.2f}, {resistance.touches}x dokunma)")
        elif resistance.touches >= 2:
            score += 1
            reasons.append(f"Guclu direnc ({resistance.price:.2f}, {resistance.touches}x dokunma)")
        else:
            reasons.append(f"Direnc yakininda ({resistance.price:.2f})")

    if macd_h < macd_h_prev and macd_h_prev > 0:
        score += 2
        reasons.append("MACD histogram asagi donuyor")
    elif macd_line < macd_sig:
        score += 1
        reasons.append("MACD sinyal altinda")

    pat = pattern_at.get(last) or pattern_at.get(last - 1)
    if pat and pat.direction == "bearish":
        score += 2
        reasons.append(f"Dusus formasyonu ({pat.name})")
        # PDF (Galen Woods): Bearish pattern AT resistance = guclu context
        if resistance and abs(close - resistance.price) / close * 100 < prox_pct:
            score += 1
            reasons.append("Formasyon direncte (Price Action context)")

    if close >= bb_upper:
        score += 1
        reasons.append("Fiyat Bollinger ust bandinda")

    if trend == "down":
        score += 1
        reasons.append("Genel trend dusus")

    if close < ema50:
        score += 1
        reasons.append("Fiyat EMA50 altinda")

    if vol > vol_avg * 1.3:
        score += 1
        reasons.append("Hacim ortalama ustu")

    # OBV trend: dusus icin OBV dusuk olmali (bearish volume)
    if not obv_trend_bullish:
        score += 1
        reasons.append("OBV dusus (hacim onayli)")

    # BB %B: ust banda yakin = iyi short firsati
    if bb_pct_b > 1.0:
        score += 1
        reasons.append("BB %B overbought")
    elif bb_pct_b > 0.7:
        score += 1
        reasons.append("BB %B yuksek bolge")

    # MA siralamasi: guclu dusus trend
    if sma9 > 0 and ema200 > 0 and close < sma9 < sma21 < ema50 < ema200:
        score += 2
        reasons.append("MA siralamasi dusus (guclu trend)")

    # Hacim yon onayi: dusus mumu + yuksek hacim
    if open_last > 0 and close < open_last and vol > vol_avg * 1.2:
        score += 1
        reasons.append("Dusus mumu + hacim onayi")

    # VWAP: fiyat VWAP altinda = short momentum
    if price_above_vwap is False:
        score += 1
        reasons.append("Fiyat VWAP altinda (momentum)")

    return score, reasons


# ---------------------------------------------------------------------------
# Setup type extraction, limit entry
# ---------------------------------------------------------------------------

def _extract_setup_type(reasons: list[str]) -> str:
    """Reasons'dan setup tipini cikar (hammer, fvg, ob, divergence, turtle, chart_pattern)."""
    reasons_str = " ".join(reasons).lower()
    if "hammer" in reasons_str or "shooting star" in reasons_str:
        return "hammer" if "hammer" in reasons_str else "shooting_star"
    if "fvg" in reasons_str or "fair value gap" in reasons_str:
        return "fvg"
    if "order block" in reasons_str or "orderblock" in reasons_str:
        return "ob"
    if "divergence" in reasons_str:
        return "divergence"
    if "turtle" in reasons_str:
        return "turtle"
    if "grafik formasyonu" in reasons_str or "formasyon" in reasons_str:
        return "chart_pattern"
    if "destek" in reasons_str or "direnc" in reasons_str:
        return "sr"
    return ""


def _compute_limit_entry(entry: float, atr_val: float, direction: str, pct: float = 0.003) -> float:
    """LONG: entry - %0.3 veya 0.3*ATR. SHORT: entry + %0.3 veya 0.3*ATR."""
    if direction == "LONG":
        limit = max(entry * (1 - pct), entry - atr_val * 0.3)
        return round(limit, 6)
    limit = min(entry * (1 + pct), entry + atr_val * 0.3)
    return round(limit, 6)


def _apply_setup_type_bonus(setup: "TradeSetup") -> None:
    """Setup tipi win rate > 55% ise guven +1."""
    if not setup or not setup.setup_type:
        return
    try:
        from signal_history import get_setup_type_stats
        stats = get_setup_type_stats(min_trades=3)
        if stats.get(setup.setup_type, 0) >= 55:
            setup.confidence = min(10, setup.confidence + 1)
            setup.reasons = list(setup.reasons) + [f"Setup tipi {setup.setup_type} %{stats[setup.setup_type]} win"]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Build trade setups with SL / TP1-2-3
# ---------------------------------------------------------------------------

def _build_long_setup(
    data, last, close, atr_val, support, resistance,
    levels, score, opp_score, reasons,
    funding_rate: Optional[float] = None,
    volatility_pct: float = 0.0,
    liquidity_warning: bool = False,
    scalp: bool = False,
    sl_widen: bool = False,
    regime: Optional[str] = None,
    adx_val: float = 25.0,
    tp1_mult: float = 1.2,
    tp2_mult: float = 2.2,
    tp3_mult: float = 3.5,
) -> TradeSetup:
    recent_low = float(data["low"].iloc[max(0, last - 5) : last + 1].min())
    sl_atr_mult = 1.0 if scalp else 1.5
    if volatility_pct > 4.0:
        sl_atr_mult *= 1.2
    if volatility_pct > 6.0:
        sl_atr_mult *= 1.1
    if sl_widen:
        sl_atr_mult *= 1.2
        reasons = list(reasons) + ["SL genisletildi (NY/London seansi)"]
    structural_sl = recent_low - atr_val * 0.2

    if support:
        sl_candidate = support.price - atr_val * (0.2 if scalp else 0.3)
        stop_loss = max(structural_sl, sl_candidate)
    else:
        stop_loss = close - atr_val * sl_atr_mult

    entry = close
    risk = entry - stop_loss

    if risk <= 0:
        risk = atr_val
        stop_loss = entry - risk

    tp1 = entry + risk * tp1_mult
    tp2 = entry + risk * tp2_mult
    tp3 = entry + risk * tp3_mult

    next_resistances = [lv.price for lv in levels if lv.price > entry and lv.kind == "resistance"]
    next_resistances.sort()
    if next_resistances and risk > 0:
        r0 = next_resistances[0]
        candidate = min(tp1, r0 * 0.999) if r0 <= tp1 else tp1
        if (candidate - entry) / risk >= tp1_mult:
            tp1 = candidate
    if len(next_resistances) >= 2 and risk > 0:
        r1 = next_resistances[1]
        candidate = min(tp2, r1 * 0.999) if r1 <= tp2 else tp2
        if (candidate - entry) / risk >= tp2_mult:
            tp2 = candidate
    if len(next_resistances) >= 3 and risk > 0:
        r2 = next_resistances[2]
        candidate = min(tp3, r2 * 0.999) if r2 <= tp3 else tp3
        if (candidate - entry) / risk >= tp3_mult:
            tp3 = candidate

    risk_pct = min(risk / entry * 100, 5.0)

    ts = data.index[last].to_pydatetime() if hasattr(data.index[last], "to_pydatetime") else datetime.now()
    conf = _realistic_confidence(score, opp_score, 4, True)

    # Candle body/wick orani: body/range < 0.3 = belirsiz mum
    o = float(data["open"].iloc[last])
    h = float(data["high"].iloc[last])
    l = float(data["low"].iloc[last])
    body = abs(close - o)
    rng = h - l
    if rng > 0 and body / rng < 0.3:
        conf = max(1, conf - 1)
        reasons.append("Belirsiz mum (body/wick dusuk)")

    if close < o:
        conf = max(1, conf - 1)
        reasons.append("Onay mumu yok (son mum dusus - LONG icin yesil beklenir)")

    # SL/TP dokunma: son 5 mumda SL veya TP1'e dokunulmus mu?
    for i in range(max(0, last - 5), last):
        low_i = float(data["low"].iloc[i])
        high_i = float(data["high"].iloc[i])
        if low_i <= stop_loss * 1.002:
            reasons.append("SL son mumlarda dokunulmus")
            conf = max(1, conf - 1)
            break
    for i in range(max(0, last - 5), last):
        high_i = float(data["high"].iloc[i])
        if high_i >= tp1 * 0.998:
            reasons.append("TP1 zaten hedeflenmis - gecikmis olabilir")
            conf = max(1, conf - 1)
            break

    # Likidite uyarisi
    if liquidity_warning:
        reasons.append("Dusuk likidite - spread yuksek olabilir")
        conf = max(1, conf - 1)

    # Funding rate: yuksek (>0.01%) LONG icin ek maliyet
    if funding_rate is not None and funding_rate > 0.0001:
        conf = max(1, conf - 1)
        reasons.append(f"Funding yuksek %{funding_rate*100:.3f} (long icin ek maliyet)")
    elif funding_rate is not None and funding_rate < -0.0001:
        reasons.append(f"Funding dusuk %{funding_rate*100:.3f} (long icin avantaj)")

    # Volatilite uyarisi: ATR/close > %4
    if volatility_pct > 4.0:
        conf = max(1, conf - 1)
        reasons.append(f"Yuksek volatilite %{volatility_pct:.1f}")

    setup_type = _extract_setup_type(reasons)
    limit_entry = _compute_limit_entry(entry, atr_val, "LONG")
    # Giris bolgesi: LONG icin destek - 0.2*ATR ile entry arasi
    entry_zone_low = support.price - atr_val * 0.2 if support else (entry - atr_val * 0.5)
    entry_zone_high = entry
    # Dinamik R:R: trending + ADX yuksek = TP1 oncelik; ranging = TP2 oncelik
    if regime == "trending" and adx_val >= 30:
        tp_priority = "tp1>tp2>tp3"
    elif regime == "ranging" or adx_val < 20:
        tp_priority = "tp2>tp1>tp3"
    else:
        tp_priority = "tp1>tp2>tp3"

    s = TradeSetup(
        time=ts,
        direction="LONG",
        confidence=conf,
        entry=round(entry, 6),
        stop_loss=round(stop_loss, 6),
        tp1=round(tp1, 6),
        tp2=round(tp2, 6),
        tp3=round(tp3, 6),
        rr1=round((tp1 - entry) / risk, 2),
        rr2=round((tp2 - entry) / risk, 2),
        rr3=round((tp3 - entry) / risk, 2),
        risk_pct=round(risk_pct, 2),
        reasons=reasons,
        limit_entry=limit_entry,
        setup_type=setup_type,
        entry_zone_low=round(entry_zone_low, 6),
        entry_zone_high=round(entry_zone_high, 6),
        tp_priority=tp_priority,
    )
    _apply_setup_type_bonus(s)
    return s


def _build_short_setup(
    data, last, close, atr_val, support, resistance,
    levels, score, opp_score, reasons,
    funding_rate: Optional[float] = None,
    volatility_pct: float = 0.0,
    liquidity_warning: bool = False,
    scalp: bool = False,
    sl_widen: bool = False,
    regime: Optional[str] = None,
    adx_val: float = 25.0,
    tp1_mult: float = 1.2,
    tp2_mult: float = 2.2,
    tp3_mult: float = 3.5,
) -> TradeSetup:
    recent_high = float(data["high"].iloc[max(0, last - 5) : last + 1].max())
    sl_atr_mult = 1.0 if scalp else 1.5
    if volatility_pct > 4.0:
        sl_atr_mult *= 1.2
    if volatility_pct > 6.0:
        sl_atr_mult *= 1.1
    if sl_widen:
        sl_atr_mult *= 1.2
        reasons = list(reasons) + ["SL genisletildi (NY/London seansi)"]
    structural_sl = recent_high + atr_val * 0.2

    if resistance:
        sl_candidate = resistance.price + atr_val * (0.2 if scalp else 0.3)
        stop_loss = min(structural_sl, sl_candidate)
    else:
        stop_loss = close + atr_val * sl_atr_mult

    entry = close
    risk = stop_loss - entry

    if risk <= 0:
        risk = atr_val
        stop_loss = entry + risk

    tp1 = entry - risk * tp1_mult
    tp2 = entry - risk * tp2_mult
    tp3 = entry - risk * tp3_mult

    next_supports = [lv.price for lv in levels if lv.price < entry and lv.kind == "support"]
    next_supports.sort(reverse=True)
    if next_supports and risk > 0:
        s0 = next_supports[0]
        candidate = max(tp1, s0 * 1.001) if s0 >= tp1 else tp1
        if (entry - candidate) / risk >= tp1_mult:
            tp1 = candidate
    if len(next_supports) >= 2 and risk > 0:
        s1 = next_supports[1]
        candidate = max(tp2, s1 * 1.001) if s1 >= tp2 else tp2
        if (entry - candidate) / risk >= tp2_mult:
            tp2 = candidate
    if len(next_supports) >= 3 and risk > 0:
        s2 = next_supports[2]
        candidate = max(tp3, s2 * 1.001) if s2 >= tp3 else tp3
        if (entry - candidate) / risk >= tp3_mult:
            tp3 = candidate

    risk_pct = min(risk / entry * 100, 5.0)

    ts = data.index[last].to_pydatetime() if hasattr(data.index[last], "to_pydatetime") else datetime.now()
    conf = _realistic_confidence(score, opp_score, 4, True)

    # Candle body/wick orani
    o = float(data["open"].iloc[last])
    h = float(data["high"].iloc[last])
    l = float(data["low"].iloc[last])
    body = abs(close - o)
    rng = h - l
    if rng > 0 and body / rng < 0.3:
        conf = max(1, conf - 1)
        reasons.append("Belirsiz mum (body/wick dusuk)")

    if close > o:
        conf = max(1, conf - 1)
        reasons.append("Onay mumu yok (son mum yukselis - SHORT icin kirmizi beklenir)")

    # SL/TP dokunma
    for i in range(max(0, last - 5), last):
        high_i = float(data["high"].iloc[i])
        if high_i >= stop_loss * 0.998:
            reasons.append("SL son mumlarda dokunulmus")
            conf = max(1, conf - 1)
            break
    for i in range(max(0, last - 5), last):
        low_i = float(data["low"].iloc[i])
        if low_i <= tp1 * 1.002:
            reasons.append("TP1 zaten hedeflenmis - gecikmis olabilir")
            conf = max(1, conf - 1)
            break

    # Likidite uyarisi
    if liquidity_warning:
        reasons.append("Dusuk likidite - spread yuksek olabilir")
        conf = max(1, conf - 1)

    # Funding rate: yuksek SHORT icin avantaj (long'lar odeme yapiyor)
    if funding_rate is not None and funding_rate > 0.0001:
        reasons.append(f"Funding yuksek %{funding_rate*100:.3f} (short icin avantaj)")
    elif funding_rate is not None and funding_rate < -0.0001:
        conf = max(1, conf - 1)
        reasons.append(f"Funding dusuk %{funding_rate*100:.3f} (short icin ek maliyet)")

    # Volatilite uyarisi
    if volatility_pct > 4.0:
        conf = max(1, conf - 1)
        reasons.append(f"Yuksek volatilite %{volatility_pct:.1f}")

    setup_type = _extract_setup_type(reasons)
    limit_entry = _compute_limit_entry(entry, atr_val, "SHORT")
    # Giris bolgesi: SHORT icin entry ile direnc + 0.2*ATR arasi
    entry_zone_low = entry
    entry_zone_high = resistance.price + atr_val * 0.2 if resistance else (entry + atr_val * 0.5)
    if regime == "trending" and adx_val >= 30:
        tp_priority = "tp1>tp2>tp3"
    elif regime == "ranging" or adx_val < 20:
        tp_priority = "tp2>tp1>tp3"
    else:
        tp_priority = "tp1>tp2>tp3"

    s = TradeSetup(
        time=ts,
        direction="SHORT",
        confidence=conf,
        entry=round(entry, 6),
        stop_loss=round(stop_loss, 6),
        tp1=round(tp1, 6),
        tp2=round(tp2, 6),
        tp3=round(tp3, 6),
        rr1=round((entry - tp1) / risk, 2),
        rr2=round((entry - tp2) / risk, 2),
        rr3=round((entry - tp3) / risk, 2),
        risk_pct=round(risk_pct, 2),
        reasons=reasons,
        limit_entry=limit_entry,
        setup_type=setup_type,
        entry_zone_low=round(entry_zone_low, 6),
        entry_zone_high=round(entry_zone_high, 6),
        tp_priority=tp_priority,
    )
    _apply_setup_type_bonus(s)
    return s


def _safe(val, default: float) -> float:
    """Skaler float doner. DataFrame/Series boolean hatasini onler."""
    if val is None:
        return default
    if isinstance(val, (pd.DataFrame, pd.Series)):
        if len(val) == 0:
            return default
        val = val.iloc[-1]
    try:
        f = float(val)
        return default if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return default


def _realistic_confidence(win_score: int, lose_score: int, threshold: int, has_setup: bool) -> int:
    """Guven skoru: skor farki kucukse dusuk guven - yanlis tahminleri azalt."""
    if not has_setup:
        lead = max(win_score, lose_score) - min(win_score, lose_score)
        base = max(win_score, lose_score)
        if base < threshold:
            return max(1, min(4, base))
        return max(1, min(5, base - threshold + 1 + (lead // 2)))
    diff = win_score - lose_score
    base = min(win_score, 10)
    if diff >= 4:
        return min(10, base + 2)
    if diff >= 2:
        return min(9, base + 1)
    if diff <= 1:
        return max(3, min(base - 1, 6))
    return max(4, min(base, 8))
