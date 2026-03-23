"""
Ekonomik Takvim Entegrasyonu
High-impact olaylar (FOMC, CPI, NFP) oncesi uyari.
Finnhub API ucretsiz tier ile (API key gerekir) veya statik kontrol.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Finnhub ucretsiz: https://finnhub.io - API key ile
# Simdilik statik "tipik" saatler ve manuel kontrol


@dataclass
class EconomicEvent:
    name: str
    impact: str  # high, medium, low
    utc_time: str  # "13:30" format
    country: str


def _typical_high_impact_times() -> list[tuple[int, int]]:
    """Tipik high-impact saatler (UTC): (saat, dakika)."""
    return [
        (13, 30),   # US NFP, CPI, Retail - Cuma/Persembe
        (15, 0),   # FOMC - Carsamba
        (19, 0),   # FOMC bazen
        (14, 0),   # ECB, PMI
    ]


def fetch_finnhub_calendar(api_key: Optional[str] = None) -> list[dict]:
    """Finnhub economic calendar - API key gerekli."""
    if not api_key:
        return []
    try:
        import requests
        today = datetime.utcnow().strftime("%Y-%m-%d")
        to_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": today, "to": to_date, "token": api_key},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return data.get("economicCalendar", [])
    except Exception:
        pass
    return []


def is_high_impact_window(minutes_window: int = 60) -> tuple[bool, str]:
    """
    Simdiki saat tipik high-impact penceresinde mi?
    Ornek: 13:30 NFP - 12:30-14:30 arasi riskli.
    """
    now = datetime.utcnow()
    h, m = now.hour, now.minute
    now_mins = h * 60 + m

    for eh, em in _typical_high_impact_times():
        event_mins = eh * 60 + em
        if abs(now_mins - event_mins) <= minutes_window:
            return True, f"High-impact penceresi (~{eh:02d}:{em:02d} UTC)"
    return False, ""


def get_economic_calendar_warning(api_key: Optional[str] = None) -> Optional[str]:
    """
    Ekonomik takvim uyarisi.
    API key yoksa sadece tipik saatleri kontrol eder.
    """
    in_window, msg = is_high_impact_window(60)
    if in_window:
        return f"UYARI: {msg} - Haber riski."

    if api_key:
        events = fetch_finnhub_calendar(api_key)
        high = [e for e in events if e.get("impact") == "high"]
        if high:
            names = [e.get("event", "?")[:30] for e in high[:3]]
            return f"Bugun high-impact: {', '.join(names)}"

    return None
