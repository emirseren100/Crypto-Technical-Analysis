"""Binance Futures API - algoritma analizi, LONG/SHORT setup + TP/SL/Entry/Kaldirac tavsiyesi."""
from dataclasses import dataclass
from typing import Literal, Optional

from analysis_runner import run_full_analysis
from backtest import BacktestResult, run_backtest
from data_fetcher import fetch_multiple_klines_parallel, fetch_usdt_symbols

DEFAULT_ACCOUNT = 100.0
MAX_LEVERAGE = 25


def _leverage_from_confidence(conf: int) -> int:
    """Daha yuksek kaldirac = daha fazla kazanc potansiyeli (analiz dogruysa)."""
    if conf >= 10:
        return min(20, MAX_LEVERAGE)
    if conf >= 9:
        return min(15, MAX_LEVERAGE)
    if conf >= 8:
        return min(12, MAX_LEVERAGE)
    if conf >= 7:
        return min(10, MAX_LEVERAGE)
    if conf >= 6:
        return min(8, MAX_LEVERAGE)
    if conf >= 5:
        return min(6, MAX_LEVERAGE)
    return min(5, MAX_LEVERAGE)


def _risk_pct_from_confidence(conf: int) -> float:
    """Daha yuksek risk = daha fazla kazanc (islem basina 10-50$ hedef, guvenle orantili)."""
    if conf >= 10:
        return 6.0
    if conf >= 9:
        return 5.0
    if conf >= 8:
        return 4.0
    if conf >= 7:
        return 3.5
    if conf >= 6:
        return 2.5
    if conf >= 5:
        return 2.0
    return 1.5


def _position_usd(
    risk_pct: float,
    sl_distance_pct: float,
    account: float = DEFAULT_ACCOUNT,
    confidence: int = 6,
) -> float:
    """Pozisyon buyuklugu. Yuksek guvende 2-2.5x hesap (TP/SL oranlari ayni, kazanc 10-50$ seviyesi)."""
    risk_usd = account * (risk_pct / 100)
    if sl_distance_pct <= 0:
        return min(account * 0.3, account)
    pos_calc = risk_usd / (sl_distance_pct / 100)
    if confidence >= 9:
        cap = account * 2.5
    elif confidence >= 7:
        cap = account * 2.0
    else:
        cap = account * 1.2
    # Minimum pozisyon: hesabin %20'si (kucuk SL'de bile anlamli kazanc), cap asilmaz
    min_pos = min(max(account * 0.2, 80.0), cap)
    return min(max(pos_calc, min_pos), cap)


@dataclass
class CoinRecommendation:
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    leverage: int
    risk_pct: float
    pos_usd: float
    confidence: int
    score: float
    win_rate: float
    profit_factor: float
    reason: str
    limit_entry: float = 0.0
    setup_type: str = ""
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    tp_priority: str = ""
    correlation_warning: str = ""


def get_recommendations(
    interval: str = "1h",
    limit: int = 250,
    max_symbols: int = 8,
    parallel_workers: int = 5,
    use_all_symbols: bool = False,
    symbols_override: list[str] | None = None,
    account: float = DEFAULT_ACCOUNT,
    strict_filter: bool = True,
) -> list[CoinRecommendation]:
    """
    Sembolleri tara, LONG/SHORT setup olanlari topla.
    Ana uygulama ile AYNI altyapi: OB, OI, liquidations, Fear&Greed, funding hist, BTC korelasyonu.
    """
    if symbols_override is not None:
        symbols = symbols_override
    elif use_all_symbols:
        symbols = _futures_symbols_with_majors_first()  # Tum Binance Futures USDT coinleri
    else:
        symbols = _top_symbols(limit=25)
    if not symbols:
        return []

    klines_map = fetch_multiple_klines_parallel(
        symbols, interval=interval, limit=limit, max_workers=parallel_workers
    )

    recs: list[CoinRecommendation] = []

    for sym in symbols:
        if len(recs) >= max_symbols:
            break
        try:
            df = klines_map.get(sym)
            if df is None or df.empty or len(df) < 60:
                continue

            try:
                res, df = run_full_analysis(
                    symbol=sym,
                    interval=interval,
                    df=df,
                    limit=limit,
                    mode="short",
                    use_symbol_calibration=True,
                    relax_adx=True,
                )
            except Exception:
                res = None

            if not res or not res.setup:
                continue

            setup = res.setup
            mtf_consensus = res.indicators.get("mtf_consensus") if res.indicators else None
            lev = _leverage_from_confidence(setup.confidence)
            risk_pct = _risk_pct_from_confidence(setup.confidence)
            pos_usd = _position_usd(risk_pct, setup.risk_pct, account=account, confidence=setup.confidence)

            bt = BacktestResult(symbol=sym, interval=interval, total_candles=len(df))
            try:
                bt = run_backtest(df, symbol=sym, interval=interval)
            except Exception:
                pass

            # Win rate / PF filtresi sadece otomatik tarama icin (strict_filter=True).
            # /paper, /detay veya coin yazinca tek sembol istenir; burada filtre uygulanmaz, setup varsa doner.
            if strict_filter:
                if bt.total_trades >= 5:
                    if bt.win_rate < 50 or bt.profit_factor < 1.3:
                        continue
                else:
                    if setup.confidence < 8:
                        continue

            pf_str = f"PF:{bt.profit_factor:.1f}" if bt.profit_factor != float("inf") else "PF:inf"
            reason_parts = [
                f"Guc {setup.confidence}/10",
                f"MTF: {mtf_consensus or '--'}",
                f"Backtest: %{bt.win_rate} basari, {pf_str}",
            ]
            if bt.total_trades >= 3 and bt.win_rate >= 50:
                reason_parts.append("Strateji etkili")
            elif bt.total_trades >= 3 and bt.win_rate < 40:
                reason_parts.append("Dikkatli kullan")
            if setup.setup_type:
                reason_parts.append(f"Setup: {setup.setup_type}")

            score = _score_coin(bt, setup.direction) + setup.confidence
            pf_val = bt.profit_factor if bt.profit_factor != float("inf") else 5.0

            rec = CoinRecommendation(
                symbol=sym,
                direction=setup.direction,
                entry=setup.entry,
                stop_loss=setup.stop_loss,
                tp1=setup.tp1,
                tp2=setup.tp2,
                tp3=setup.tp3,
                leverage=lev,
                risk_pct=risk_pct,
                pos_usd=pos_usd,
                confidence=setup.confidence,
                score=score,
                win_rate=bt.win_rate,
                profit_factor=pf_val,
                reason=" | ".join(reason_parts),
                limit_entry=getattr(setup, "limit_entry", 0.0) or 0.0,
                setup_type=getattr(setup, "setup_type", "") or "",
                entry_zone_low=getattr(setup, "entry_zone_low", 0.0) or 0.0,
                entry_zone_high=getattr(setup, "entry_zone_high", 0.0) or 0.0,
                tp_priority=getattr(setup, "tp_priority", "") or "",
                correlation_warning="",
            )
            recs.append(rec)
        except Exception:
            continue

    recs.sort(key=lambda r: (r.confidence, r.score), reverse=True)

    # Korelasyon uyarisi: 3+ LONG onerisi ve yuksek korelasyon = risk
    long_recs = [r for r in recs if r.direction == "LONG"]
    if len(long_recs) >= 3:
        try:
            from correlation_matrix import compute_correlation_matrix
            symbols = [r.symbol for r in long_recs[:6]]
            corr_res = compute_correlation_matrix(symbols, interval=interval, limit=100)
            if corr_res.warning and len(corr_res.correlated_pairs) >= 2:
                warn = "UYARI: 3+ LONG korele - ayni anda pozisyon riski. " + (corr_res.warning or "")
                for r in long_recs:
                    r.correlation_warning = warn
        except Exception:
            pass

    return recs


def _normalize_symbol(s: str) -> str:
    """BTC, btc, BTCUSDT -> BTCUSDT."""
    s = s.upper().strip()
    return s + "USDT" if not s.endswith("USDT") else s


def _top_symbols(limit: int = 30) -> list[str]:
    """Oncelikli semboller - hizli liste (cok API cagrisi yapmaz)."""
    return [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT",
        "PEPEUSDT", "SHIBUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
        "SUIUSDT", "NEARUSDT", "INJUSDT", "FILUSDT", "RENDERUSDT",
        "WLDUSDT", "FETUSDT", "TAOUSDT", "BONKUSDT", "FLOKIUSDT",
    ][:limit]


def _futures_symbols_with_majors_first(max_count: Optional[int] = None) -> list[str]:
    """
    Binance Futures API'den tum TRADING USDT paritelerini al.
    Majörler (BTC, ETH, SOL...) once, sonra digerleri.
    max_count=None ise tum coinler; sayi verilirse o kadar.
    """
    all_syms = fetch_usdt_symbols()
    majors = _top_symbols(limit=50)
    priority = [s for s in majors if s in all_syms]
    rest = [s for s in all_syms if s not in priority]
    result = priority + rest
    return result[:max_count] if max_count is not None else result


def get_recommendation_for_symbol(
    symbol: str,
    interval: str = "30m",
    limit: int = 250,
    account: float | None = None,
) -> CoinRecommendation | None:
    """
    Tek bir coin icin analiz. Entry, SL, TP gercek signal_engine + backtest sonucu.
    account verilmezse DEFAULT_ACCOUNT kullanilir.
    """
    recs = get_recommendations(
        interval=interval,
        limit=limit,
        max_symbols=1,
        symbols_override=[_normalize_symbol(symbol)],
        account=account if account is not None else DEFAULT_ACCOUNT,
        strict_filter=False,
    )
    return recs[0] if recs else None


def _score_coin(bt: BacktestResult, direction: str) -> float:
    """Oneri skoru: backtest + yon."""
    pf = bt.profit_factor if bt.profit_factor != float("inf") else 5
    base = pf * 5 + bt.win_rate * 0.3 + bt.total_pnl_pct * 0.1
    if direction == "LONG":
        base += 2
    elif direction == "SHORT":
        base += 1.5
    return base
