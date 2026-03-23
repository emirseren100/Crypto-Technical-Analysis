"""Fiyat formatlama - memecoin ve yuksek fiyatli coinler icin dogru kusurat."""


def price_precision(price: float | None) -> int:
    """Binance ile uyumlu kusurat: dusuk fiyatlar 5-8 basamak."""
    if price is None:
        return 2
    p = float(price)
    if p >= 1000:
        return 2
    if p >= 100:
        return 2
    if p >= 1:
        return 4
    if p >= 0.1:
        return 5
    if p >= 0.01:
        return 5
    if p >= 0.0001:
        return 6
    return 8


def format_price(price: float | None) -> str:
    """Fiyati uygun kusuratla formatla."""
    prec = price_precision(price)
    return f"{float(price or 0):.{prec}f}"
