"""
Zamanlama ve Seans Filtreleri (Session Dynamics)
- Hafta sonu: Cumartesi/Pazar dusuk hacim, fakeout cok
- NY/London acilisi: 15:30-17:00 Turkiye = 12:30-14:00 UTC (yuksek volatilite)
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SessionResult:
    is_weekend: bool
    is_ny_london_open: bool
    turkey_hour: int
    utc_hour: int
    session_warning: str | None
    sl_widen_suggested: bool
    min_confluence_suggested: int


def get_turkey_hour() -> int:
    """Turkiye saati (UTC+3)."""
    return (datetime.utcnow().hour + 3) % 24


def is_weekend() -> bool:
    """Cumartesi (5) veya Pazar (6)."""
    return datetime.utcnow().weekday() >= 5


def is_ny_london_open() -> bool:
    """NY/London acilisi: Turkiye 15:30-17:00 = UTC 12:30-14:00."""
    utc_hour = datetime.utcnow().hour
    utc_min = datetime.utcnow().minute
    utc_decimal = utc_hour + utc_min / 60
    return 12.5 <= utc_decimal < 14.0


def analyze_session() -> SessionResult:
    """
    Hafta sonu: daha temkinli, sinyal onay sayisini artir.
    NY/London: volatilite yuksek, SL genislet veya onay artir.
    """
    weekend = is_weekend()
    ny_london = is_ny_london_open()
    utc_hour = datetime.utcnow().hour
    turkey_hour = get_turkey_hour()

    warning = None
    sl_widen = False
    min_conf = 6

    if weekend:
        warning = "Hafta sonu - dusuk hacim, fakeout riski yuksek. Daha temkinli calis."
        min_conf = 7
    elif ny_london:
        warning = "NY/London acilisi (15:30-17:00 TR) - yuksek volatilite. SL genislet veya onay artir."
        sl_widen = True
        min_conf = 6

    return SessionResult(
        is_weekend=weekend,
        is_ny_london_open=ny_london,
        turkey_hour=turkey_hour,
        utc_hour=utc_hour,
        session_warning=warning,
        sl_widen_suggested=sl_widen,
        min_confluence_suggested=min_conf,
    )
