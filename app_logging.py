"""
Uygulama loglama - hata ve onemli olaylar dosyaya yazilir.
Log klasorunu degistirmek icin LOG_DIR sabitini duzenle veya setup_logging(log_dir=...) ile gec.
"""
import logging
import sys
from pathlib import Path

# Log dosyalarinin yazilacagi klasor. None = cwd, veya Path/str ile ozel klasor
LOG_DIR: Path | str | None = Path(__file__).parent / "logs"


def setup_logging(
    log_dir: str | Path | None = None,
    level: int = logging.INFO,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """
    Loglama yapilandir. Dosya: binance_ta.log (rotating).
    Returns: root logger
    """
    if log_dir is None:
        log_dir = LOG_DIR if LOG_DIR is not None else Path.cwd()
    log_path = Path(log_dir) / "binance_ta.log"
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("binance_ta")
    root.setLevel(level)
    if root.handlers:
        return root

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    return root


def get_logger(name: str) -> logging.Logger:
    """Alt modul icin logger al."""
    return logging.getLogger(f"binance_ta.{name}")
