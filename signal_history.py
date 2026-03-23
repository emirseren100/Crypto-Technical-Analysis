import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

DB_PATH = Path(__file__).parent / "signals.db"


@dataclass
class SignalRecord:
    id: int
    timestamp: str
    symbol: str
    interval: str
    direction: Literal["AL", "SAT", "BEKLE"]
    strength: int
    reasons: str
    price_at_signal: float
    price_after: Optional[float] = None
    result_pct: Optional[float] = None


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            interval        TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            strength        INTEGER NOT NULL,
            reasons         TEXT    NOT NULL,
            price_at_signal REAL    NOT NULL,
            price_after     REAL,
            result_pct      REAL
        )
    """)
    try:
        conn.execute("ALTER TABLE signals ADD COLUMN mode TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE signals ADD COLUMN setup_type TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def save_signal(
    symbol: str,
    interval: str,
    direction: str,
    strength: int,
    reasons: list[str],
    price: float,
    mode: str = "short",
    setup_type: str = "",
) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO signals
               (timestamp, symbol, interval, direction, strength, reasons, price_at_signal, mode, setup_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(timespec="seconds"),
                symbol,
                interval,
                direction,
                strength,
                " | ".join(reasons),
                price,
                mode,
                setup_type or "",
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def update_result(signal_id: int, current_price: float) -> None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT price_at_signal, direction FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            return

        orig_price, direction = row
        if orig_price == 0:
            return

        if direction in ("AL", "LONG"):
            pct = (current_price - orig_price) / orig_price * 100
        elif direction in ("SAT", "SHORT"):
            pct = (orig_price - current_price) / orig_price * 100
        else:
            pct = 0.0

        conn.execute(
            "UPDATE signals SET price_after = ?, result_pct = ? WHERE id = ?",
            (current_price, round(pct, 4), signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(
    symbol: Optional[str] = None,
    limit: int = 100,
) -> list[SignalRecord]:
    conn = _get_conn()
    try:
        cols = "id, timestamp, symbol, interval, direction, strength, reasons, price_at_signal, price_after, result_pct"
        if symbol:
            rows = conn.execute(
                f"SELECT {cols} FROM signals WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {cols} FROM signals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [SignalRecord(*r) for r in rows]
    finally:
        conn.close()


def get_stats(symbol: Optional[str] = None) -> dict:
    conn = _get_conn()
    try:
        where = "WHERE symbol = ?" if symbol else ""
        params: tuple = (symbol,) if symbol else ()

        total = conn.execute(
            f"SELECT COUNT(*) FROM signals {where}", params
        ).fetchone()[0]

        buy_count = conn.execute(
            f"SELECT COUNT(*) FROM signals {where} {'AND' if where else 'WHERE'} direction IN ('AL','LONG')",
            params,
        ).fetchone()[0]

        sell_count = conn.execute(
            f"SELECT COUNT(*) FROM signals {where} {'AND' if where else 'WHERE'} direction IN ('SAT','SHORT')",
            params,
        ).fetchone()[0]

        wait_count = total - buy_count - sell_count

        profitable = conn.execute(
            f"SELECT COUNT(*) FROM signals {where} "
            f"{'AND' if where else 'WHERE'} result_pct IS NOT NULL AND result_pct > 0",
            params,
        ).fetchone()[0]

        evaluated = conn.execute(
            f"SELECT COUNT(*) FROM signals {where} "
            f"{'AND' if where else 'WHERE'} result_pct IS NOT NULL",
            params,
        ).fetchone()[0]

        avg_result = conn.execute(
            f"SELECT AVG(result_pct) FROM signals {where} "
            f"{'AND' if where else 'WHERE'} result_pct IS NOT NULL",
            params,
        ).fetchone()[0]

        return {
            "total": total,
            "buy": buy_count,
            "sell": sell_count,
            "wait": wait_count,
            "evaluated": evaluated,
            "profitable": profitable,
            "win_rate": round(profitable / evaluated * 100, 1) if evaluated > 0 else 0,
            "avg_result_pct": round(avg_result or 0, 4),
        }
    finally:
        conn.close()


def get_calibration_stats(
    symbol: Optional[str] = None,
    min_evaluated: int = 15,
    mode: Optional[str] = None,
) -> dict:
    """Guven bandina gore win rate. mode=scalp icin ayri kalibrasyon."""
    conn = _get_conn()
    try:
        where = "result_pct IS NOT NULL AND direction IN ('AL','LONG','SAT','SHORT')"
        params: list = []
        if symbol:
            where = f"symbol = ? AND {where}"
            params.append(symbol)
        if mode:
            if mode == "short":
                where = f"({where}) AND (mode = 'short' OR mode IS NULL)"
            else:
                where = f"({where}) AND mode = ?"
                params.append(mode)
        params = tuple(params)
        rows = conn.execute(
            f"SELECT strength, result_pct FROM signals WHERE {where}",
            params,
        ).fetchall()
    finally:
        conn.close()

    trade_count = 0
    try:
        from trade_results import get_trade_rows_for_calibration
        trade_rows = get_trade_rows_for_calibration()
        trade_count = len(trade_rows)
        rows = list(rows) + [(r[0], r[1]) for r in trade_rows]
    except Exception:
        pass

    if len(rows) < min_evaluated:
        return {"calibrated_min": 6, "by_band": {}, "total": len(rows), "total_trades": trade_count}

    by_band: dict[str, list[float]] = {"4-5": [], "6-7": [], "8-10": []}
    for r in rows:
        strength, result_pct = r[0], r[1]
        if strength <= 5:
            by_band["4-5"].append(result_pct)
        elif strength <= 7:
            by_band["6-7"].append(result_pct)
        else:
            by_band["8-10"].append(result_pct)

    win_rates = {}
    for band, results in by_band.items():
        if results:
            wins = sum(1 for x in results if x > 0)
            win_rates[band] = round(wins / len(results) * 100, 1)

    calibrated_min = 6
    if win_rates.get("4-5", 0) < 35 and win_rates.get("6-7", 0) < 40:
        calibrated_min = 8
    elif win_rates.get("4-5", 0) < 40:
        calibrated_min = 7

    return {"calibrated_min": calibrated_min, "by_band": win_rates, "total": len(rows), "total_trades": trade_count}


def get_setup_type_stats(min_trades: int = 3) -> dict[str, float]:
    """
    Setup tipine gore win rate. hammer, fvg, ob, divergence, turtle, chart_pattern, sr.
    Returns: {"hammer": 58.5, "fvg": 52.0, ...}
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT setup_type, result_pct FROM signals
               WHERE result_pct IS NOT NULL AND direction IN ('AL','LONG','SAT','SHORT')
               AND setup_type IS NOT NULL AND setup_type != ''"""
        ).fetchall()
    finally:
        conn.close()

    by_type: dict[str, list[float]] = {}
    for setup_type, result_pct in rows:
        st = (setup_type or "").strip() or "other"
        if st not in by_type:
            by_type[st] = []
        by_type[st].append(float(result_pct))

    result = {}
    for st, pnls in by_type.items():
        if len(pnls) >= min_trades:
            wins = sum(1 for p in pnls if p > 0)
            result[st] = round(wins / len(pnls) * 100, 1)
    return result


def get_session_win_rates(symbol: Optional[str] = None, min_trades: int = 5) -> dict:
    """
    Seans bazli win rate: Asia (00-07 UTC), London (08-15 UTC), NY (16-23 UTC).
    Donus: { "asia": {"win_rate": 45, "trades": 10}, "london": {...}, "ny": {...}, "current": "london" }
    """
    conn = _get_conn()
    try:
        where = "AND symbol = ?" if symbol else ""
        params: tuple = (symbol,) if symbol else ()
        rows = conn.execute(
            f"""SELECT timestamp, result_pct FROM signals
                WHERE result_pct IS NOT NULL AND direction IN ('AL','LONG','SAT','SHORT') {where}
                ORDER BY id DESC LIMIT 500""",
            params,
        ).fetchall()
    finally:
        conn.close()

    sessions = {"asia": [], "london": [], "ny": []}
    for ts, result_pct in rows:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour = dt.hour if hasattr(dt, "hour") else int(ts[11:13]) if len(ts) >= 13 else 12
        except Exception:
            hour = 12
        if 0 <= hour <= 7:
            sessions["asia"].append(float(result_pct))
        elif 8 <= hour <= 15:
            sessions["london"].append(float(result_pct))
        else:
            sessions["ny"].append(float(result_pct))

    result = {}
    for name, pnls in sessions.items():
        wins = sum(1 for p in pnls if p > 0)
        n = len(pnls)
        result[name] = {"win_rate": round(wins / n * 100, 1) if n >= min_trades else 0, "trades": n}

    try:
        from datetime import timezone
        now = datetime.now(timezone.utc).hour
    except Exception:
        now = 12
    if 0 <= now <= 7:
        result["current"] = "asia"
    elif 8 <= now <= 15:
        result["current"] = "london"
    else:
        result["current"] = "ny"
    return result
