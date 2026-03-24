"""
TP profilleri: ayni analiz/SL mantigi; yalnizca TP1/TP2/TP3 risk (R) carpani degisir.
"""
from typing import Tuple

# Swing (kisa/uzun vade): mevcut varsayilan = normal
_SWING_NORMAL: Tuple[float, float, float] = (1.2, 2.2, 3.5)
_SWING_AGGRESSIVE: Tuple[float, float, float] = (1.45, 2.65, 4.2)
_SWING_CONSERVATIVE: Tuple[float, float, float] = (1.0, 1.85, 3.0)

# Scalp: TP1 normalde 0.9R (TP2/TP3 swing ile ayni); profil swing oranina gore olceklenir
_SCALP_TP1_BASE = 0.9


def normalize_tp_profile(profile: str) -> str:
    """Bilinmeyen degerler 'normal' sayilir."""
    p = (profile or "normal").strip().lower()
    if p in ("aggressive", "agresif", "risk", "yuksek", "high"):
        return "aggressive"
    if p in ("conservative", "muhafazakar", "tight", "dusuk", "low", "guvenli"):
        return "conservative"
    return "normal"


def get_tp_multipliers(profile: str, scalp: bool) -> Tuple[float, float, float]:
    """
    Donus: (tp1_mult, tp2_mult, tp3_mult) — risk = |entry - stop_loss| ile carpilir.
    Scalp modunda TP1, normal swing TP1 oranina gore olceklenir (0.9/1.2).
    """
    key = normalize_tp_profile(profile)
    if key == "aggressive":
        swing = _SWING_AGGRESSIVE
    elif key == "conservative":
        swing = _SWING_CONSERVATIVE
    else:
        swing = _SWING_NORMAL
    if not scalp:
        return swing
    scale_tp1 = _SCALP_TP1_BASE / _SWING_NORMAL[0]
    return (swing[0] * scale_tp1, swing[1], swing[2])
