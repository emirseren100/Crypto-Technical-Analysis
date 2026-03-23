"""Acilis uyarisi - kripto trade uygulamasi tarzi."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from theme import BG_DARK, BG_PANEL, BORDER, GREEN, TEXT, TEXT_MUTED


class DisclaimerDialog(QDialog):
    """Profesyonel acilis uyarisi dialogu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Önemli Bilgilendirme")
        self.setFixedSize(480, 320)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_DARK};
            }}
            QLabel {{
                color: {TEXT};
                font-size: 11px;
                line-height: 1.5;
            }}
            QPushButton {{
                background-color: {GREEN};
                color: #0b0e11;
                border: none;
                border-radius: 6px;
                padding: 12px 28px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #1ed191;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(28, 28, 28, 28)

        title = QLabel("⚡ Kripto Teknik Analiz Uygulaması")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        title.setStyleSheet(f"color: {TEXT};")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {BORDER}; max-height: 1px;")
        layout.addWidget(sep)

        msg = QLabel(
            "Bu uygulama yatırım tavsiyesi değildir.\n\n"
            "Gösterilen setup'lar tamamen matematiksel koşul eşleşmelerine dayanır "
            "ve kesin kar garantisi vermez.\n\n"
            "Risk yönetimi (stop-loss, pozisyon büyüklüğü) "
            "her zaman sizin sorumluluğunuzdadır."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(msg, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("Anladım, Devam Et")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
