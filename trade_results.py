"""Gercek islem sonuclari - kullanicinin girdigi trade'ler. Kalibrasyon icin kullanilir."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

DB_PATH = Path(__file__).parent / "signals.db"


@dataclass
class TradeResult:
    id: int
    timestamp: str
    symbol: str
    interval: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    exit_price: float
    result_pct: float
    confidence: int
    notes: str = ""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            interval        TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            exit_price      REAL    NOT NULL,
            result_pct      REAL    NOT NULL,
            confidence      INTEGER NOT NULL,
            notes           TEXT
        )
    """)
    conn.commit()
    return conn


def add_trade_result(
    symbol: str,
    interval: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    confidence: int,
    notes: str = "",
) -> int:
    if direction.upper() in ("LONG", "AL"):
        result_pct = (exit_price - entry_price) / entry_price * 100
    else:
        result_pct = (entry_price - exit_price) / entry_price * 100

    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO trade_results
               (timestamp, symbol, interval, direction, entry_price, exit_price, result_pct, confidence, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(timespec="seconds"),
                symbol,
                interval,
                "LONG" if direction.upper() in ("LONG", "AL") else "SHORT",
                entry_price,
                exit_price,
                round(result_pct, 4),
                confidence,
                notes,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def get_trade_results(limit: int = 100) -> list[TradeResult]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, symbol, interval, direction, entry_price, exit_price, result_pct, confidence, notes FROM trade_results ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            TradeResult(
                id=r[0], timestamp=r[1], symbol=r[2], interval=r[3], direction=r[4],
                entry_price=r[5], exit_price=r[6], result_pct=r[7], confidence=r[8],
                notes=r[9] or "",
            )
            for r in rows
        ]
    finally:
        conn.close()


def delete_trade_result(trade_id: int) -> bool:
    """Belirtilen islemi siler."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM trade_results WHERE id = ?", (trade_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_trade_rows_for_calibration() -> list[tuple[int, float]]:
    """(confidence, result_pct) listesi - signal_history tarafindan kullanilir."""
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT confidence, result_pct FROM trade_results"
        ).fetchall()
    finally:
        conn.close()
