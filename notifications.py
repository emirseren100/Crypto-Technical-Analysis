"""
Bildirim modulu: Windows (plyer).
"""
from typing import Optional

from format_utils import price_precision


def send_windows_notification(title: str, message: str, app_name: str = "Binance Teknik Analiz") -> bool:
    """Windows sistem bildirimi (plyer)."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name=app_name, timeout=8)
        return True
    except Exception:
        return False


def notify_setup(
    symbol: str,
    interval: str,
    direction: str,
    entry: float,
    confidence: int,
    windows: bool = True,
    stop_loss: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    tp3: Optional[float] = None,
    limit_entry: Optional[float] = None,
) -> None:
    """Setup bildirimi: Windows toast."""
    prec = price_precision(entry)
    title = f"{direction} Setup - {symbol}"
    msg_short = f"{interval} | Guven: {confidence}/10 | Entry: {entry:.{prec}f}"
    if windows:
        send_windows_notification(title, msg_short)
