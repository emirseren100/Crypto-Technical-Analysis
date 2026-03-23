import sys
import traceback

from PyQt5.QtCore import Qt
from PyQt5.QtCore import QSettings
from app_logging import setup_logging, get_logger
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication, QMessageBox

from main_window import MainWindow
from theme import BG_MAIN, BG_PANEL, TEXT, global_stylesheet


def _excepthook(exc_type, exc_value, exc_tb):
    """Yakalanmamis hatalari yakala - uygulama kapanmasin."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        setup_logging()
        get_logger("app").error("Yakalanmamis hata: %s", msg)
    except Exception:
        print("Yakalanmamis hata:", msg)
    try:
        app = QApplication.instance()
        if app:
            QMessageBox.warning(
                None, "Hata",
                f"Beklenmeyen bir hata olustu:\n\n{exc_type.__name__}: {exc_value}\n\nUygulama calismaya devam edebilir."
            )
    except Exception:
        pass


def main() -> None:
    sys.excepthook = _excepthook
    setup_logging()

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG_MAIN))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(BG_PANEL))
    pal.setColor(QPalette.AlternateBase, QColor(BG_PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(BG_PANEL))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    app.setPalette(pal)

    dark = QSettings("BinanceTA", "TeknikAnaliz").value("theme_dark", True, type=bool)
    app.setStyleSheet(global_stylesheet(dark=dark))

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
