"""
Raporlama - Haftalik/aylik performans, Excel/PDF export, sinyal gecmisi filtreleme.
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from signal_history import get_history, get_stats
from trade_results import get_trade_results


def get_history_filtered(
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    min_strength: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 200,
) -> list:
    """Sinyal gecmisi filtrele: sembol, yon, guven, tarih araligi."""
    records = get_history(symbol=symbol, limit=limit * 2)
    if direction:
        dir_map = {"LONG": ("AL", "LONG"), "SHORT": ("SAT", "SHORT")}
        allowed = dir_map.get(direction.upper(), (direction,))
        records = [r for r in records if r.direction in allowed]
    if min_strength is not None:
        records = [r for r in records if r.strength >= min_strength]
    if date_from or date_to:
        filtered = []
        for r in records:
            try:
                dt = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
                if date_from and dt.replace(tzinfo=None) < date_from:
                    continue
                if date_to and dt.replace(tzinfo=None) > date_to:
                    continue
                filtered.append(r)
            except Exception:
                filtered.append(r)
        records = filtered
    return records[:limit]


def get_weekly_stats() -> dict:
    """Son 7 gun istatistikleri."""
    since = datetime.now() - timedelta(days=7)
    records = get_history_filtered(date_from=since, limit=500)
    evaluated = [r for r in records if r.result_pct is not None]
    wins = sum(1 for r in evaluated if r.result_pct and r.result_pct > 0)
    total_pnl = sum(r.result_pct or 0 for r in evaluated)
    return {
        "total_signals": len(records),
        "evaluated": len(evaluated),
        "wins": wins,
        "win_rate": round(wins / len(evaluated) * 100, 1) if evaluated else 0,
        "total_pnl_pct": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(evaluated), 2) if evaluated else 0,
    }


def get_monthly_stats() -> dict:
    """Son 30 gun istatistikleri."""
    since = datetime.now() - timedelta(days=30)
    records = get_history_filtered(date_from=since, limit=1000)
    evaluated = [r for r in records if r.result_pct is not None]
    wins = sum(1 for r in evaluated if r.result_pct and r.result_pct > 0)
    total_pnl = sum(r.result_pct or 0 for r in evaluated)
    return {
        "total_signals": len(records),
        "evaluated": len(evaluated),
        "wins": wins,
        "win_rate": round(wins / len(evaluated) * 100, 1) if evaluated else 0,
        "total_pnl_pct": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(evaluated), 2) if evaluated else 0,
    }


def export_to_csv(records: list, filepath: Path) -> bool:
    """Sinyal kayitlarini CSV'ye aktar."""
    try:
        import csv
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "timestamp", "symbol", "interval", "direction", "strength", "reasons", "price_at_signal", "result_pct"])
            for r in records:
                w.writerow([
                    getattr(r, "id", ""),
                    r.timestamp,
                    r.symbol,
                    r.interval,
                    r.direction,
                    r.strength,
                    (r.reasons or "")[:200],
                    r.price_at_signal,
                    r.result_pct or "",
                ])
        return True
    except Exception:
        return False


def export_to_excel(records: list, filepath: Path) -> bool:
    """Sinyal kayitlarini Excel'e aktar (openpyxl veya xlsxwriter)."""
    try:
        import pandas as pd
        rows = []
        for r in records:
            rows.append({
                "Tarih": r.timestamp,
                "Sembol": r.symbol,
                "Interval": r.interval,
                "Yon": r.direction,
                "Guven": r.strength,
                "Fiyat": r.price_at_signal,
                "Sonuc %": r.result_pct or "",
            })
        df = pd.DataFrame(rows)
        df.to_excel(filepath, index=False, engine="openpyxl")
        return True
    except ImportError:
        return export_to_csv(records, filepath.with_suffix(".csv"))
    except Exception:
        return False


def generate_report_text(period: str = "weekly") -> str:
    """Metin raporu: haftalik veya aylik."""
    if period == "monthly":
        stats = get_monthly_stats()
        title = "Aylik Rapor (30 gun)"
    else:
        stats = get_weekly_stats()
        title = "Haftalik Rapor (7 gun)"
    trade_rows = get_trade_results(limit=50)
    trade_pnl = sum(getattr(r, "result_pct", 0) or 0 for r in trade_rows)
    lines = [
        f"=== {title} ===",
        f"Toplam sinyal: {stats['total_signals']}",
        f"Degerlendirilen: {stats['evaluated']}",
        f"Kazanan: {stats['wins']}",
        f"Win rate: %{stats['win_rate']}",
        f"Toplam PnL %: {stats['total_pnl_pct']}",
        f"Ortalama PnL: %{stats['avg_pnl']}",
        f"Gercek islem PnL: %{round(trade_pnl, 2)}",
    ]
    try:
        from signal_history import get_setup_type_stats
        setup_stats = get_setup_type_stats(min_trades=3)
        if setup_stats:
            lines.append("")
            lines.append("--- Setup Tipi Win Rate ---")
            for st, wr in sorted(setup_stats.items(), key=lambda x: -x[1]):
                lines.append(f"  {st}: %{wr}")
    except Exception:
        pass
    return "\n".join(lines)
