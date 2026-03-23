from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from indicators import compute_all
from signal_engine import score_at_index


@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    entry_time: str
    direction: Literal["LONG", "SHORT"]
    exit_idx: int = 0
    exit_price: float = 0.0
    exit_time: str = ""
    pnl_pct: float = 0.0
    exit_reason: str = ""
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    total_candles: int
    trades: list[Trade] = field(default_factory=list)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    max_win_pct: float = 0.0
    max_loss_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0


def run_backtest(
    df: pd.DataFrame,
    symbol: str = "",
    interval: str = "",
    stop_loss_atr_mult: float = 1.5,
    take_profit_atr_mult: float = 2.5,
    min_signal_strength: int = 4,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    scalp: bool = False,
) -> BacktestResult:
    if len(df) < 60:
        return BacktestResult(symbol=symbol, interval=interval, total_candles=len(df))

    try:
        from config import get_config
        cfg = get_config()
        slippage_pct = cfg.slippage_pct
    except ImportError:
        pass

    if scalp and min_signal_strength < 5:
        min_signal_strength = 5

    data = compute_all(df, scalp=scalp)

    trades: list[Trade] = []
    in_trade = False
    current_trade: Trade | None = None

    for i in range(50, len(data) - 1):
        close_i = float(data["close"].iloc[i])
        atr_i = float(data["atr"].iloc[i]) if pd.notna(data["atr"].iloc[i]) else 0

        if in_trade and current_trade and atr_i > 0:
            risk = stop_loss_atr_mult * atr_i
            low_i = float(data["low"].iloc[i])
            high_i = float(data["high"].iloc[i])

            if current_trade.direction == "LONG":
                tp1_mult = 0.9 if scalp else 1.0
                sl = current_trade.entry_price - risk
                tp1 = current_trade.entry_price + risk * tp1_mult
                tp2 = current_trade.entry_price + risk * 2.0
                tp3 = current_trade.entry_price + risk * 3.0

                if low_i <= sl:
                    exit_price = sl * (1 - slippage_pct / 100)
                    pnl = (exit_price - current_trade.entry_price) / current_trade.entry_price * 100 - commission_pct * 2
                    current_trade.exit_idx = i
                    current_trade.exit_price = exit_price
                    current_trade.exit_time = str(data.index[i])
                    current_trade.pnl_pct = round(pnl, 4)
                    current_trade.exit_reason = "Stop-Loss"
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    continue

                if high_i >= tp1 and not current_trade.tp1_hit:
                    current_trade.tp1_hit = True
                if high_i >= tp2 and not current_trade.tp2_hit:
                    current_trade.tp2_hit = True
                if high_i >= tp3 and not current_trade.tp3_hit:
                    current_trade.tp3_hit = True

                if current_trade.tp1_hit and current_trade.tp2_hit and current_trade.tp3_hit:
                    tp1_adj = tp1 * (1 - slippage_pct / 100)
                    tp2_adj = tp2 * (1 - slippage_pct / 100)
                    tp3_adj = tp3 * (1 - slippage_pct / 100)
                    pnl = (
                        (tp1_adj - current_trade.entry_price) / current_trade.entry_price * 100 / 3
                        + (tp2_adj - current_trade.entry_price) / current_trade.entry_price * 100 / 3
                        + (tp3_adj - current_trade.entry_price) / current_trade.entry_price * 100 / 3
                        - commission_pct * 2
                    )
                    current_trade.exit_idx = i
                    current_trade.exit_price = tp3_adj
                    current_trade.exit_time = str(data.index[i])
                    current_trade.pnl_pct = round(pnl, 4)
                    current_trade.exit_reason = "TP1+TP2+TP3"
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    continue

            elif current_trade.direction == "SHORT":
                tp1_mult = 0.9 if scalp else 1.0
                sl = current_trade.entry_price + risk
                tp1 = current_trade.entry_price - risk * tp1_mult
                tp2 = current_trade.entry_price - risk * 2.0
                tp3 = current_trade.entry_price - risk * 3.0

                if high_i >= sl:
                    exit_price = sl * (1 + slippage_pct / 100)
                    pnl = (current_trade.entry_price - exit_price) / current_trade.entry_price * 100 - commission_pct * 2
                    current_trade.exit_idx = i
                    current_trade.exit_price = exit_price
                    current_trade.exit_time = str(data.index[i])
                    current_trade.pnl_pct = round(pnl, 4)
                    current_trade.exit_reason = "Stop-Loss"
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    continue

                if low_i <= tp1 and not current_trade.tp1_hit:
                    current_trade.tp1_hit = True
                if low_i <= tp2 and not current_trade.tp2_hit:
                    current_trade.tp2_hit = True
                if low_i <= tp3 and not current_trade.tp3_hit:
                    current_trade.tp3_hit = True

                if current_trade.tp1_hit and current_trade.tp2_hit and current_trade.tp3_hit:
                    tp1_adj = tp1 * (1 + slippage_pct / 100)
                    tp2_adj = tp2 * (1 + slippage_pct / 100)
                    tp3_adj = tp3 * (1 + slippage_pct / 100)
                    pnl = (
                        (current_trade.entry_price - tp1_adj) / current_trade.entry_price * 100 / 3
                        + (current_trade.entry_price - tp2_adj) / current_trade.entry_price * 100 / 3
                        + (current_trade.entry_price - tp3_adj) / current_trade.entry_price * 100 / 3
                        - commission_pct * 2
                    )
                    current_trade.exit_idx = i
                    current_trade.exit_price = tp3_adj
                    current_trade.exit_time = str(data.index[i])
                    current_trade.pnl_pct = round(pnl, 4)
                    current_trade.exit_reason = "TP1+TP2+TP3"
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    continue

            continue

        if in_trade:
            continue

        buy_score, sell_score = score_at_index(data, df, i, proximity_pct=1.5, scalp=scalp)

        if buy_score >= min_signal_strength and buy_score > sell_score:
            raw_entry = float(data["open"].iloc[i + 1])
            entry_price = raw_entry * (1 + slippage_pct / 100)
            current_trade = Trade(
                entry_idx=i + 1,
                entry_price=entry_price,
                entry_time=str(data.index[i + 1]),
                direction="LONG",
            )
            in_trade = True

        elif sell_score >= min_signal_strength and sell_score > buy_score:
            raw_entry = float(data["open"].iloc[i + 1])
            entry_price = raw_entry * (1 - slippage_pct / 100)
            current_trade = Trade(
                entry_idx=i + 1,
                entry_price=entry_price,
                entry_time=str(data.index[i + 1]),
                direction="SHORT",
            )
            in_trade = True

    if in_trade and current_trade:
        last_close = float(data["close"].iloc[-1])
        risk = float(data["atr"].iloc[-1]) * stop_loss_atr_mult if pd.notna(data["atr"].iloc[-1]) else 0
        if risk <= 0:
            risk = abs(last_close - current_trade.entry_price) * 0.5
        tp1_mult = 0.9 if scalp else 1.0
        pnl = 0.0
        if current_trade.direction == "LONG":
            tp1 = current_trade.entry_price + risk * tp1_mult
            tp2 = current_trade.entry_price + risk * 2
            tp3 = current_trade.entry_price + risk * 3
            if current_trade.tp1_hit:
                pnl += (tp1 - current_trade.entry_price) / current_trade.entry_price * 100 / 3
            if current_trade.tp2_hit:
                pnl += (tp2 - current_trade.entry_price) / current_trade.entry_price * 100 / 3
            if current_trade.tp3_hit:
                pnl += (tp3 - current_trade.entry_price) / current_trade.entry_price * 100 / 3
            remaining = (3 - current_trade.tp1_hit - current_trade.tp2_hit - current_trade.tp3_hit) / 3
            exit_adj = last_close * (1 - slippage_pct / 100)
            pnl += remaining * (exit_adj - current_trade.entry_price) / current_trade.entry_price * 100
        else:
            tp1 = current_trade.entry_price - risk * tp1_mult
            tp2 = current_trade.entry_price - risk * 2
            tp3 = current_trade.entry_price - risk * 3
            if current_trade.tp1_hit:
                pnl += (current_trade.entry_price - tp1) / current_trade.entry_price * 100 / 3
            if current_trade.tp2_hit:
                pnl += (current_trade.entry_price - tp2) / current_trade.entry_price * 100 / 3
            if current_trade.tp3_hit:
                pnl += (current_trade.entry_price - tp3) / current_trade.entry_price * 100 / 3
            remaining = (3 - current_trade.tp1_hit - current_trade.tp2_hit - current_trade.tp3_hit) / 3
            exit_adj = last_close * (1 + slippage_pct / 100)
            pnl += remaining * (current_trade.entry_price - exit_adj) / current_trade.entry_price * 100
        pnl -= commission_pct * 2
        current_trade.exit_idx = len(data) - 1
        current_trade.exit_price = last_close
        current_trade.exit_time = str(data.index[-1])
        current_trade.pnl_pct = round(pnl, 4)
        current_trade.exit_reason = "Acik (TP1/2/3 kismi)"
        trades.append(current_trade)

    return _build_result(trades, symbol, interval, len(data), slippage_pct)


def _build_result(
    trades: list[Trade],
    symbol: str,
    interval: str,
    total_candles: int,
    slippage_pct: float = 0.0,
) -> BacktestResult:
    result = BacktestResult(
        symbol=symbol,
        interval=interval,
        total_candles=total_candles,
        trades=trades,
        total_trades=len(trades),
    )

    if not trades:
        return result

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0
    result.total_pnl_pct = round(sum(t.pnl_pct for t in trades), 4)
    result.avg_pnl_pct = round(result.total_pnl_pct / len(trades), 4)
    result.max_win_pct = round(max((t.pnl_pct for t in trades), default=0), 4)
    result.max_loss_pct = round(min((t.pnl_pct for t in trades), default=0), 4)

    gross_profit = sum(t.pnl_pct for t in wins)
    gross_loss = abs(sum(t.pnl_pct for t in losses))
    result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum_pnl += t.pnl_pct
        peak = max(peak, cum_pnl)
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown_pct = round(max_dd, 4)
    result.calmar_ratio = round(result.total_pnl_pct / max_dd, 2) if max_dd > 0 else (float("inf") if result.total_pnl_pct > 0 else 0.0)

    return result


def optimize_backtest(
    df: pd.DataFrame,
    symbol: str = "",
    interval: str = "",
    commission_pct: float = 0.1,
    scalp: bool = False,
) -> tuple[dict, BacktestResult]:
    """Grid search: en iyi SL, TP, min_signal kombinasyonu."""
    if scalp:
        sl_opts = [0.8, 1.0, 1.2]
        tp_opts = [1.5, 2.0, 2.5]
        min_str_opts = [5, 6, 7]
    else:
        sl_opts = [1.0, 1.25, 1.5, 1.75, 2.0]
        tp_opts = [2.0, 2.5, 3.0, 3.5]
        min_str_opts = [4, 5, 6]
    best_score = -999
    best_params: dict = {}
    best_result: BacktestResult | None = None

    for sl in sl_opts:
        for tp in tp_opts:
            for ms in min_str_opts:
                if tp <= sl * 1.5:
                    continue
                r = run_backtest(
                    df, symbol=symbol, interval=interval,
                    stop_loss_atr_mult=sl,
                    take_profit_atr_mult=tp,
                    min_signal_strength=ms,
                    commission_pct=commission_pct,
                    scalp=scalp,
                )
                if r.total_trades < 5:
                    continue
                score = r.profit_factor * 10 + r.win_rate * 0.5 + r.total_pnl_pct * 0.1
                if score > best_score:
                    best_score = score
                    best_params = {"sl": sl, "tp": tp, "min_str": ms}
                    best_result = r

    if best_result is None:
        best_params = {"sl": 1.0 if scalp else 1.5, "tp": 2.0 if scalp else 2.5, "min_str": 5 if scalp else 4}
        best_result = run_backtest(df, symbol=symbol, interval=interval, scalp=scalp)
    return best_params, best_result


def get_symbol_performance(
    symbols: list[str],
    interval: str = "1h",
    limit: int = 300,
    scalp: bool = False,
) -> list[tuple[str, BacktestResult]]:
    """Sembol bazli backtest performansi. En iyiden en kotuye."""
    from data_fetcher import safe_fetch_klines

    results: list[tuple[str, BacktestResult]] = []
    for sym in symbols[:15]:
        try:
            df = safe_fetch_klines(sym, interval, limit)
            if len(df) < 60:
                continue
            r = run_backtest(df, symbol=sym, interval=interval, scalp=scalp)
            results.append((sym, r))
        except Exception:
            continue

    def _score(r):
        pf = r.profit_factor if r.profit_factor != float("inf") else 10
        return pf * 10 + r.win_rate * 0.5 + r.total_pnl_pct
    results.sort(key=lambda x: _score(x[1]), reverse=True)
    return results
