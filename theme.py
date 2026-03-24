"""Kripto trade uygulamasi - Binance tarzi koyu tema + acik tema secenegi."""

# Koyu tema (varsayilan)
BG_DARK = "#0b0e11"
BG_MAIN = "#131722"
BG_PANEL = "#1e232d"
BG_HOVER = "#2b2f3a"
BORDER = "#2b2f3a"
TEXT = "#eaecef"
TEXT_MUTED = "#848e9c"
GREEN = "#0ecb81"
RED = "#f6465d"
ORANGE = "#f0b90b"
BLUE = "#3861fb"

# Acik tema
LIGHT = {
    "BG_DARK": "#e8eaed",
    "BG_MAIN": "#ffffff",
    "BG_PANEL": "#f1f3f4",
    "BG_HOVER": "#e8eaed",
    "BORDER": "#dadce0",
    "TEXT": "#202124",
    "TEXT_MUTED": "#5f6368",
    "GREEN": "#1e8e3e",
    "RED": "#d93025",
    "ORANGE": "#f9ab00",
    "BLUE": "#1a73e8",
}


def global_stylesheet(dark: bool = True) -> str:
    """Tum uygulama icin global stylesheet. dark=False acik tema."""
    if not dark:
        c = LIGHT
        return f"""
    QMainWindow, QWidget {{
        background-color: {c['BG_MAIN']};
        color: {c['TEXT']};
    }}
    QTabWidget::pane {{
        border: 1px solid {c['BORDER']};
        border-radius: 6px;
        background-color: {c['BG_PANEL']};
        margin-top: -1px;
        padding: 8px;
    }}
    QTabBar::tab {{
        background-color: {c['BG_PANEL']};
        color: {c['TEXT_MUTED']};
        padding: 10px 18px;
        margin-right: 4px;
        min-height: 22px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background-color: {c['BG_MAIN']};
        color: {c['TEXT']};
        border-bottom: 2px solid {c['GREEN']};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {c['BG_HOVER']};
        color: {c['TEXT']};
    }}
    QGroupBox {{
        border: 1px solid {c['BORDER']};
        border-radius: 8px;
        margin-top: 14px;
        padding: 16px 12px 12px 12px;
        font-weight: 600;
        color: {c['TEXT']};
        background-color: {c['BG_PANEL']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 8px;
        color: {c['TEXT']};
        font-size: 11px;
    }}
    QPushButton {{
        background-color: {c['GREEN']};
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px 18px;
        font-weight: 600;
        font-size: 11px;
        min-height: 32px;
        min-width: 72px;
    }}
    QPushButton:hover {{ background-color: #34a853; }}
    QPushButton:pressed {{ background-color: #0d652d; }}
    QPushButton:disabled {{ background-color: {c['BG_HOVER']}; color: {c['TEXT_MUTED']}; }}
    QPushButton[secondary="true"] {{ background-color: {c['BG_HOVER']}; color: {c['TEXT']}; }}
    QPushButton[secondary="true"]:hover {{ background-color: #dadce0; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {c['BG_PANEL']};
        color: {c['TEXT']};
        border: 1px solid {c['BORDER']};
        border-radius: 6px;
        min-height: 30px;
        font-size: 11px;
        padding: 6px 10px;
    }}
    QTableWidget {{ background-color: {c['BG_PANEL']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']}; }}
    QHeaderView::section {{ background-color: {c['BG_DARK']}; color: {c['TEXT_MUTED']}; }}
    QCheckBox {{ color: {c['TEXT']}; font-size: 11px; }}
    QStatusBar {{
        background-color: {c['BG_DARK']};
        color: {c['TEXT_MUTED']};
        font-size: 11px;
        padding: 4px 8px;
        min-height: 24px;
    }}
    QLabel {{ color: {c['TEXT']}; font-size: 11px; }}
    QToolTip {{ background-color: {c['BG_DARK']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']}; font-size: 11px; }}
    """
    return f"""
    QMainWindow, QWidget {{
        background-color: {BG_MAIN};
        color: {TEXT};
    }}
    QTabWidget::pane {{
        border: 1px solid {BORDER};
        border-radius: 6px;
        background-color: {BG_PANEL};
        margin-top: -1px;
        padding: 8px;
    }}
    QTabBar::tab {{
        background-color: {BG_PANEL};
        color: {TEXT_MUTED};
        padding: 10px 18px;
        margin-right: 4px;
        min-height: 22px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background-color: {BG_MAIN};
        color: {TEXT};
        border-bottom: 2px solid {GREEN};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {BG_HOVER};
        color: {TEXT};
    }}
    QGroupBox {{
        border: 1px solid {BORDER};
        border-radius: 8px;
        margin-top: 14px;
        padding: 16px 12px 12px 12px;
        font-weight: 600;
        color: {TEXT};
        background-color: {BG_PANEL};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 8px;
        color: {TEXT};
        font-size: 11px;
    }}
    QPushButton {{
        background-color: {GREEN};
        color: #0b0e11;
        border: none;
        border-radius: 6px;
        padding: 10px 18px;
        font-weight: 600;
        font-size: 11px;
        min-height: 32px;
        min-width: 72px;
    }}
    QPushButton:hover {{
        background-color: #1ed191;
    }}
    QPushButton:pressed {{
        background-color: #0ba870;
    }}
    QPushButton:disabled {{
        background-color: {BG_HOVER};
        color: {TEXT_MUTED};
    }}
    QPushButton[secondary="true"] {{
        background-color: {BG_HOVER};
        color: {TEXT};
    }}
    QPushButton[secondary="true"]:hover {{
        background-color: #3a3f4b;
    }}
    QComboBox {{
        background-color: {BG_PANEL};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 8px 12px;
        min-height: 30px;
        min-width: 120px;
        font-size: 11px;
    }}
    QComboBox:hover {{
        border-color: {TEXT_MUTED};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
        background-color: {BG_PANEL};
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        color: {TEXT};
        selection-background-color: {BG_HOVER};
        padding: 4px;
    }}
    QComboBox QLineEdit {{
        background-color: {BG_PANEL};
        color: {TEXT};
        border: none;
        padding: 4px 8px;
        font-size: 11px;
    }}
    QLineEdit {{
        background-color: {BG_PANEL};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 8px 12px;
        min-height: 30px;
        min-width: 80px;
        font-size: 11px;
    }}
    QLineEdit:focus {{
        border-color: {GREEN};
    }}
    QSpinBox, QDoubleSpinBox {{
        background-color: {BG_PANEL};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 30px;
        font-size: 11px;
    }}
    QTableWidget {{
        background-color: {BG_PANEL};
        color: {TEXT};
        gridline-color: {BORDER};
        border: 1px solid {BORDER};
        border-radius: 6px;
        font-size: 11px;
    }}
    QTableWidget::item {{
        padding: 8px 10px;
        min-height: 24px;
    }}
    QTableWidget::item:selected {{
        background-color: {BG_HOVER};
    }}
    QHeaderView::section {{
        background-color: {BG_DARK};
        color: {TEXT_MUTED};
        padding: 10px 12px;
        font-size: 11px;
        font-weight: 600;
        border: none;
        border-bottom: 2px solid {BORDER};
    }}
    QCheckBox {{
        color: {TEXT};
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 4px;
        border: 2px solid {BORDER};
        background-color: {BG_PANEL};
    }}
    QCheckBox::indicator:checked {{
        background-color: {GREEN};
        border-color: {GREEN};
    }}
    QStatusBar {{
        background-color: {BG_DARK};
        color: {TEXT_MUTED};
        font-size: 11px;
        padding: 4px 8px;
        min-height: 24px;
    }}
    QLabel {{
        color: {TEXT};
        font-size: 11px;
    }}
    QScrollBar:vertical {{
        background-color: {BG_PANEL};
        width: 10px;
        border-radius: 5px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background-color: {BORDER};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {TEXT_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QSplitter::handle {{
        background-color: {BORDER};
        width: 2px;
        height: 2px;
    }}
    QToolTip {{
        background-color: {BG_DARK};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 6px;
        font-size: 10px;
    }}
    QMessageBox {{
        background-color: {BG_MAIN};
    }}
    QMessageBox QLabel {{
        color: {TEXT};
    }}
    """
