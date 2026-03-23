"""
Paper Trading - Sanal pozisyon ac/kapa, PnL takibi.
Gercek para kullanmadan sinyalleri test etmek icin.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

DB_PATH = Path(__file__).parent / "paper_trades.db"


@dataclass
class PaperPosition:
    id: int
    symbol: str
    interval: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    entry_time: str
    sl: float
    tp1: float
    tp2: float
    tp3: float
    position_usd: float
    confidence: int
    status: Literal["open", "closed"]
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    pnl_pct: Optional[float] = None


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            interval        TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            entry_time      TEXT    NOT NULL,
            sl              REAL    NOT NULL,
            tp1             REAL    NOT NULL,
            tp2             REAL    NOT NULL,
            tp3             REAL    NOT NULL,
            position_usd    REAL    NOT NULL DEFAULT 100,
            confidence      INTEGER NOT NULL DEFAULT 6,
            status          TEXT    NOT NULL DEFAULT 'open',
            exit_price      REAL,
            exit_time       TEXT,
            exit_reason     TEXT,
            pnl_pct         REAL
        )
    """)
    conn.commit()
    return conn


def open_position(
    symbol: str,
    interval: str,
    direction: Literal["LONG", "SHORT"],
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    position_usd: float = 100.0,
    confidence: int = 6,
) -> int:
    """Yeni paper pozisyon ac. Ayni sembolde acik pozisyon varsa acmaz; -1 doner."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM paper_trades WHERE symbol = ? AND status = 'open'",
            (symbol,),
        ).fetchone()
        if row:
            return -1
        cur = conn.execute(
            """INSERT INTO paper_trades
               (symbol, interval, direction, entry_price, entry_time, sl, tp1, tp2, tp3, position_usd, confidence, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (
                symbol,
                interval,
                direction,
                entry,
                datetime.now().isoformat(timespec="seconds"),
                sl,
                tp1,
                tp2,
                tp3,
                position_usd,
                confidence,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def _row_to_position(row: tuple) -> PaperPosition:
    return PaperPosition(
        id=row[0],
        symbol=row[1],
        interval=row[2],
        direction=row[3],
        entry_price=row[4],
        entry_time=row[5],
        sl=row[6],
        tp1=row[7],
        tp2=row[8],
        tp3=row[9],
        position_usd=row[10],
        confidence=row[11],
        status=row[12],
        exit_price=row[13],
        exit_time=row[14],
        exit_reason=row[15],
        pnl_pct=row[16],
    )


def get_open_positions() -> list[PaperPosition]:
    """Acik paper pozisyonlari."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, symbol, interval, direction, entry_price, entry_time, sl, tp1, tp2, tp3, position_usd, confidence, status, exit_price, exit_time, exit_reason, pnl_pct FROM paper_trades WHERE status = 'open' ORDER BY entry_time DESC"
        ).fetchall()
        return [_row_to_position(r) for r in rows]
    finally:
        conn.close()


def get_closed_trades(limit: int = 100) -> list[PaperPosition]:
    """Kapanmis paper islemler (en yeni once)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, symbol, interval, direction, entry_price, entry_time, sl, tp1, tp2, tp3, position_usd, confidence, status, exit_price, exit_time, exit_reason, pnl_pct
               FROM paper_trades WHERE status = 'closed' ORDER BY exit_time DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_row_to_position(r) for r in rows]
    finally:
        conn.close()


def get_summary() -> dict:
    """Ozet: toplam PnL %, kazanan/kaybeden sayisi, win rate."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT pnl_pct FROM paper_trades WHERE status = 'closed' AND pnl_pct IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"total_pnl_pct": 0.0, "win_count": 0, "loss_count": 0, "win_rate": 0.0, "total_trades": 0}
    pnls = [r[0] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)
    total_pnl = sum(pnls)
    return {
        "total_pnl_pct": round(total_pnl, 2),
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / len(pnls) * 100, 1) if pnls else 0.0,
        "total_trades": len(pnls),
    }


def check_and_close_positions(symbol: str, current_price: float) -> list[PaperPosition]:
    """
    Sembol icin guncel fiyata gore acik pozisyonu kontrol et.
    SL veya TP1/TP2/TP3 vurulduysa kapat. Ilk vurulan seviyede tam kapanis.
    Donus: kapanan pozisyonlar (bos liste olabilir).
    """
    conn = _get_conn()
    closed: list[PaperPosition] = []
    try:
        rows = conn.execute(
            "SELECT id, symbol, interval, direction, entry_price, entry_time, sl, tp1, tp2, tp3, position_usd, confidence, status, exit_price, exit_time, exit_reason, pnl_pct FROM paper_trades WHERE symbol = ? AND status = 'open'",
            (symbol,),
        ).fetchall()
        for row in rows:
            pos = _row_to_position(row)
            exit_price = None
            exit_reason = None
            if pos.direction == "LONG":
                if current_price <= pos.sl:
                    exit_price = pos.sl
                    exit_reason = "SL"
                elif current_price >= pos.tp3:
                    exit_price = pos.tp3
                    exit_reason = "TP3"
                elif current_price >= pos.tp2:
                    exit_price = pos.tp2
                    exit_reason = "TP2"
                elif current_price >= pos.tp1:
                    exit_price = pos.tp1
                    exit_reason = "TP1"
            else:
                if current_price >= pos.sl:
                    exit_price = pos.sl
                    exit_reason = "SL"
                elif current_price <= pos.tp3:
                    exit_price = pos.tp3
                    exit_reason = "TP3"
                elif current_price <= pos.tp2:
                    exit_price = pos.tp2
                    exit_reason = "TP2"
                elif current_price <= pos.tp1:
                    exit_price = pos.tp1
                    exit_reason = "TP1"
            if exit_price is not None and exit_reason:
                pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100 if pos.direction == "LONG" else (pos.entry_price - exit_price) / pos.entry_price * 100
                conn.execute(
                    """UPDATE paper_trades SET status = 'closed', exit_price = ?, exit_time = ?, exit_reason = ?, pnl_pct = ? WHERE id = ?""",
                    (exit_price, datetime.now().isoformat(timespec="seconds"), exit_reason, round(pnl_pct, 4), pos.id),
                )
                pos.status = "closed"
                pos.exit_price = exit_price
                pos.exit_time = datetime.now().isoformat(timespec="seconds")
                pos.exit_reason = exit_reason
                pos.pnl_pct = round(pnl_pct, 4)
                closed.append(pos)
        conn.commit()
    finally:
        conn.close()
    return closed


def close_position_manually(position_id: int, exit_price: float) -> bool:
    """Pozisyonu manuel kapat (kullanici butonu)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, direction, entry_price FROM paper_trades WHERE id = ? AND status = 'open'",
            (position_id,),
        ).fetchone()
        if not row:
            return False
        _, direction, entry_price = row
        pnl_pct = (exit_price - entry_price) / entry_price * 100 if direction == "LONG" else (entry_price - exit_price) / entry_price * 100
        conn.execute(
            """UPDATE paper_trades SET status = 'closed', exit_price = ?, exit_time = ?, exit_reason = 'Manuel', pnl_pct = ? WHERE id = ?""",
            (exit_price, datetime.now().isoformat(timespec="seconds"), round(pnl_pct, 4), position_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def has_open_position(symbol: str) -> bool:
    """Sembolde acik paper pozisyon var mi."""
    conn = _get_conn()
    try:
        return conn.execute("SELECT 1 FROM paper_trades WHERE symbol = ? AND status = 'open'", (symbol,)).fetchone() is not None
    finally:
        conn.close()
