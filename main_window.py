import time
from datetime import datetime

import pandas as pd
from PyQt5.QtCore import QSettings, QThread, QTimer, Qt, pyqtSignal, QObject, QStringListModel
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QCompleter,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backtest import BacktestResult, get_symbol_performance, optimize_backtest, run_backtest
from chart_prediction import ChartPredictionWidget, PredictionResult, get_prediction
from chart_widget import ChartWidget
from data_fetcher import (
    INTERVALS,
    fetch_btc_dominance,
    fetch_exchange_flow_signal,
    fetch_fear_greed,
    fetch_funding_rate,
    fetch_funding_rate_history,
    fetch_liquidations,
    fetch_open_interest,
    fetch_order_book_imbalance,
    fetch_prev_day_high_low,
    fetch_ticker_24h,
    fetch_ticker_price,
    fetch_usdt_symbols,
    search_symbols,
    safe_fetch_klines,
)
from correlation_matrix import compute_correlation_matrix
from economic_calendar import get_economic_calendar_warning
from multi_timeframe import MTFResult, run_mtf_analysis
from signal_engine import AnalysisResult, TradeSetup, analyze
from report_generator import export_to_csv, export_to_excel, generate_report_text, get_history_filtered, get_monthly_stats, get_weekly_stats
from signal_history import get_calibration_stats, get_history, get_session_win_rates, get_stats, save_signal, update_result
from coin_recommendations import CoinRecommendation, get_recommendations
from trade_results import add_trade_result, delete_trade_result, get_trade_results
from paper_trading import (
    open_position as paper_open_position,
    get_open_positions,
    get_closed_trades,
    get_summary,
    check_and_close_positions as paper_check_close,
    has_open_position as paper_has_open,
)
from ws_client import BinanceWebSocket
from disclaimer_dialog import DisclaimerDialog
from theme import BG_HOVER, BG_PANEL, BLUE, GREEN, ORANGE, RED, TEXT


_DIRECTION_COLORS = {
    "LONG": GREEN,
    "SHORT": RED,
    "AL": GREEN,
    "SAT": RED,
    "BEKLE": ORANGE,
}


MAX_LEVERAGE = 20


def _leverage_from_confidence(conf: int) -> int:
    """Güven puanına göre kaldıraç (max 20x)."""
    if conf >= 10:
        return min(15, MAX_LEVERAGE)
    if conf >= 9:
        return min(12, MAX_LEVERAGE)
    if conf >= 8:
        return min(10, MAX_LEVERAGE)
    if conf >= 7:
        return min(8, MAX_LEVERAGE)
    if conf >= 6:
        return min(5, MAX_LEVERAGE)
    if conf >= 5:
        return min(4, MAX_LEVERAGE)
    if conf >= 4:
        return min(3, MAX_LEVERAGE)
    return 2


def _risk_pct_from_confidence(conf: int) -> float:
    """Güven puanına göre risk % (islem basina 10-50$ hedef, coin_recommendations ile uyumlu)."""
    if conf >= 10:
        return 6.0
    if conf >= 9:
        return 5.0
    if conf >= 8:
        return 4.0
    if conf >= 7:
        return 3.5
    if conf >= 6:
        return 2.5
    if conf >= 5:
        return 2.0
    return 1.5


def _position_usd_from_risk(
    risk_pct: float, sl_distance_pct: float, account_size: float, confidence: int = 6
) -> float:
    """Risk $ = hesap * risk%, pozisyon = risk$ / (SL %). Yuksek guvende 2-2.5x hesap cap (coin_recommendations ile uyumlu)."""
    risk_usd = account_size * (risk_pct / 100)
    if sl_distance_pct <= 0:
        return min(account_size * 0.3, account_size)
    pos_calc = risk_usd / (sl_distance_pct / 100)
    if confidence >= 9:
        cap = account_size * 2.5
    elif confidence >= 7:
        cap = account_size * 2.0
    else:
        cap = account_size * 1.2
    min_pos = min(max(account_size * 0.2, 80.0), cap)
    return min(max(pos_calc, min_pos), cap)


class _WsBridge(QObject):
    kline_received = pyqtSignal(dict)
    ticker_received = pyqtSignal(dict)
    status_received = pyqtSignal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kripto Teknik Analiz  |  Price Action & Grafik")
        self.setMinimumSize(1400, 880)
        self._df: pd.DataFrame = pd.DataFrame()
        self._last_result: AnalysisResult | None = None
        self._last_analysis_symbol: str = ""
        self._last_analysis_interval: str = ""
        self._main_analyses_in_direction: int = 0
        self._main_last_direction: str = ""
        self._last_signal_id: int = 0
        self._last_notified_key: str = ""
        self._weak_symbols: set[str] = set()

        self._ws = BinanceWebSocket()
        self._ws_bridge = _WsBridge()
        self._ws_bridge.kline_received.connect(self._on_ws_kline)
        self._ws_bridge.ticker_received.connect(self._on_ws_ticker)
        self._ws_bridge.status_received.connect(self._on_ws_status)

        self._live_price = 0.0
        self._market_context_cache: dict = {}
        self._market_context_ts: float = 0
        self._rec_data: list = []
        self._scalp_analysis_running = False
        self._last_scalp_result: AnalysisResult | None = None
        self._last_scalp_symbol: str = ""
        self._last_scalp_interval: str = ""
        self._scalp_analyses_in_direction: int = 0
        self._scalp_last_direction: str = ""

        self._build_ui()
        self._show_disclaimer()
        self._load_symbols()
        self._setup_timer()
        self._setup_market_context_timer()
        self._setup_scalp_timers()
        self._on_interval_changed(self._combo_interval.currentText())
        self._on_refresh()

    def closeEvent(self, event) -> None:
        """Pencere kapanirken timer ve WS durdur - crash onle."""
        try:
            if hasattr(self, "_timer") and self._timer.isActive():
                self._timer.stop()
            if hasattr(self, "_market_timer") and self._market_timer.isActive():
                self._market_timer.stop()
            if hasattr(self, "_scalp_price_timer") and self._scalp_price_timer.isActive():
                self._scalp_price_timer.stop()
            if hasattr(self, "_scalp_analysis_timer") and self._scalp_analysis_timer.isActive():
                self._scalp_analysis_timer.stop()
            pt = getattr(self, "_price_timer", None)
            if pt and hasattr(pt, "isActive") and pt.isActive():
                self._price_timer.stop()
            if hasattr(self, "_ws") and self._ws.is_running:
                self._ws.disconnect()
        except Exception:
            pass
        event.accept()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        root.addLayout(self._build_left_panel(), 0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_chart_tab(), "Grafik")
        self._tabs.addTab(self._build_prediction_tab(), "Grafik Tahmini")
        self._tabs.addTab(self._build_scalp_tab(), "Scalp")
        self._tabs.addTab(self._build_mtf_tab(), "Coklu Zaman Dilimi")
        self._tabs.addTab(self._build_history_tab(), "Sinyal Gecmisi")
        self._tabs.addTab(self._build_trade_results_tab(), "Gercek Islemler")
        self._tabs.addTab(self._build_paper_tab(), "Paper Trading")
        self._tabs.addTab(self._build_position_calc_tab(), "Pozisyon Hesaplayici")
        self._tabs.addTab(self._build_backtest_tab(), "Backtest")
        self._tabs.addTab(self._build_correlation_tab(), "Korelasyon Matrisi")
        self._tabs.addTab(self._build_news_tab(), "Haber / Olaylar")
        self._tabs.addTab(self._build_recommendations_tab(), "Oneriler")
        self._tabs.addTab(self._build_report_tab(), "Rapor")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs, 1)

        root.addLayout(self._build_right_panel(), 0)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_label = QLabel("Hazir")
        self._status.addWidget(self._status_label, 1)
        self._ws_status_label = QLabel("WS: Kapalı")
        self._ws_status_label.setStyleSheet(f"color: {ORANGE};")
        self._status.addWidget(self._ws_status_label)

    # --- Left panel ---

    def _build_left_panel(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        grp = QGroupBox("Ayarlar")
        grp.setMinimumWidth(220)
        grp.setMaximumWidth(280)
        grp_layout = QVBoxLayout(grp)
        _settings = QSettings("BinanceTA", "TeknikAnaliz")

        grp_layout.addWidget(QLabel("Parite Ara"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Örn: BTC, SOL, DOGE...")
        self._search_input.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._search_completer = QCompleter()
        self._search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._search_completer.setMaxVisibleItems(15)
        self._search_completer.setFilterMode(Qt.MatchContains)
        popup = self._search_completer.popup()
        popup.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT}; border: 1px solid #2b2f3a;")
        self._search_input.setCompleter(self._search_completer)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._on_search_enter)
        grp_layout.addWidget(self._search_input)

        self._combo_symbol = QComboBox()
        self._combo_symbol.setMaxVisibleItems(20)
        self._combo_symbol.setMinimumWidth(160)
        self._combo_symbol.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_symbol.currentTextChanged.connect(self._on_symbol_changed)
        grp_layout.addWidget(self._combo_symbol)

        grp_layout.addSpacing(6)
        grp_layout.addWidget(QLabel("Analiz Modu"))
        self._combo_mode = QComboBox()
        self._combo_mode.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_mode.addItems(["Kisa Vade (15m/1h)", "Uzun Vade (4h/1d)", "Scalp (5m/15m)"])
        self._combo_mode.setToolTip("Kisa vade: dusuk esik. Uzun vade: yuksek esik, swing. Scalp: 5m/15m, dar SL/TP, hacim onayi.")
        grp_layout.addWidget(self._combo_mode)

        grp_layout.addWidget(QLabel("Zaman Dilimi"))
        self._combo_interval = QComboBox()
        self._combo_interval.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_interval.addItems(INTERVALS)
        self._combo_interval.setCurrentText("1h")
        grp_layout.addWidget(self._combo_interval)
        self._interval_warning_label = QLabel("")
        self._interval_warning_label.setStyleSheet(f"color: {ORANGE}; font-size: 9px;")
        self._interval_warning_label.setWordWrap(True)
        grp_layout.addWidget(self._interval_warning_label)
        self._interval_tip_label = QLabel("Öneri: 15m / 1h / 4h - daha az gürültü")
        self._interval_tip_label.setStyleSheet(f"color: {GREEN}; font-size: 9px;")
        grp_layout.addWidget(self._interval_tip_label)
        self._combo_interval.currentTextChanged.connect(self._on_interval_changed)

        grp_layout.addWidget(QLabel("Mum Sayisi"))
        self._combo_limit = QComboBox()
        self._combo_limit.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_limit.addItems(["100", "200", "300", "500", "750", "1000"])
        self._combo_limit.setCurrentText("300")
        grp_layout.addWidget(self._combo_limit)

        self._btn_refresh = QPushButton("Analiz Et")
        self._btn_refresh.clicked.connect(self._on_refresh)
        grp_layout.addWidget(self._btn_refresh)

        grp_layout.addSpacing(12)

        self._btn_ws = QPushButton("WebSocket Başlat")
        self._btn_ws.setProperty("secondary", "true")
        self._btn_ws.clicked.connect(self._toggle_ws)
        grp_layout.addWidget(self._btn_ws)

        self._live_price_label = QLabel("Canli: --")
        self._live_price_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self._live_price_label.setAlignment(Qt.AlignCenter)
        self._live_price_label.setWordWrap(True)
        grp_layout.addWidget(self._live_price_label)

        grp_layout.addSpacing(8)
        grp_layout.addWidget(QLabel("Hesap Buyuklugu ($)"))
        self._spin_account_size = QSpinBox()
        self._spin_account_size.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._spin_account_size.setRange(10, 1_000_000)
        self._spin_account_size.setValue(_settings.value("account_size", 100, type=int))
        self._spin_account_size.setSuffix(" $")
        self._spin_account_size.valueChanged.connect(self._on_account_size_changed)
        grp_layout.addWidget(self._spin_account_size)
        self._balance_label = QLabel(f"Risk: güven 6-7→%1, 8-9→%1.5, 10→%2 | Max kaldıraç: {MAX_LEVERAGE}x")
        self._balance_label.setStyleSheet(f"color: {GREEN}; font-size: 10px;")
        self._balance_label.setWordWrap(True)
        grp_layout.addWidget(self._balance_label)

        self._market_context_label = QLabel("Fear & Greed: -- | BTC Dom: --")
        self._market_context_label.setStyleSheet(f"color: {BLUE}; font-size: 9px;")
        self._market_context_label.setWordWrap(True)
        grp_layout.addWidget(self._market_context_label)
        self._market_context_comment = QLabel("")
        self._market_context_comment.setStyleSheet("color: #9e9e9e; font-size: 8px; font-style: italic;")
        self._market_context_comment.setWordWrap(True)
        grp_layout.addWidget(self._market_context_comment)

        grp_layout.addSpacing(6)
        grp_layout.addWidget(QLabel("Min. Guven (kalibre uygulanir)"))
        self._spin_min_conf = QSpinBox()
        self._spin_min_conf.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._spin_min_conf.setRange(4, 10)
        self._spin_min_conf.setValue(_settings.value("min_confidence", 6, type=int))
        self._spin_min_conf.valueChanged.connect(self._on_min_conf_changed)
        grp_layout.addWidget(self._spin_min_conf)
        self._calib_label = QLabel("")
        self._calib_label.setStyleSheet("color: #9e9e9e; font-size: 8px;")
        grp_layout.addWidget(self._calib_label)

        grp_layout.addSpacing(6)
        grp_layout.addWidget(QLabel("Pozisyonum (ters sinyal uyarisi icin)"))
        self._combo_my_position = QComboBox()
        self._combo_my_position.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_my_position.addItems(["Yok", "LONG", "SHORT"])
        grp_layout.addWidget(self._combo_my_position)

        grp_layout.addSpacing(8)
        self._chk_notifications = QCheckBox("Bildirimleri ac (ses + popup)")
        self._chk_notifications.setChecked(_settings.value("notifications_enabled", True, type=bool))
        self._chk_notifications.stateChanged.connect(self._on_notifications_toggled)
        grp_layout.addWidget(self._chk_notifications)

        grp_layout.addSpacing(6)
        grp_layout.addWidget(QLabel("Tema"))
        self._combo_theme = QComboBox()
        self._combo_theme.addItems(["Koyu", "Acik"])
        self._combo_theme.setCurrentIndex(0 if _settings.value("theme_dark", True, type=bool) else 1)
        self._combo_theme.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        grp_layout.addWidget(self._combo_theme)

        self._btn_favorite = QPushButton("Favorilere Ekle")
        self._btn_favorite.setProperty("secondary", True)
        self._btn_favorite.setStyleSheet(f"background-color: {BG_HOVER}; color: {TEXT};")
        self._btn_favorite.clicked.connect(self._on_add_favorite)
        grp_layout.addWidget(self._btn_favorite)

        grp_layout.addStretch()
        layout.addWidget(grp)
        layout.addStretch()
        return layout

    # --- Chart tab ---

    def _build_chart_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        btn_row = QHBoxLayout()
        self._btn_save_png = QPushButton("PNG Kaydet")
        self._btn_save_png.clicked.connect(self._on_save_chart_png)
        btn_row.addWidget(self._btn_save_png)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self._chart = ChartWidget()
        lay.addWidget(self._chart)
        return w

    def _on_save_chart_png(self) -> None:
        symbol = self._combo_symbol.currentText() or "BTCUSDT"
        interval = self._combo_interval.currentText()
        setup = self._last_result.setup if self._last_result and self._last_result.setup else None
        path, _ = QFileDialog.getSaveFileName(
            self, "Grafik PNG Kaydet",
            f"{symbol}_{interval}.png",
            "PNG (*.png)",
        )
        if path:
            result = self._chart.save_to_png(path, symbol=symbol, interval=interval, setup=setup)
            if result:
                self._status_label.setText(f"Kaydedildi: {result}")
            else:
                self._status_label.setText("PNG kaydetme hatasi")

    # --- Grafik Tahmini tab ---

    def _build_prediction_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Parite"))
        self._pred_symbol = QComboBox()
        self._pred_symbol.setEditable(True)
        self._pred_symbol.setMinimumWidth(140)
        self._pred_symbol.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._pred_symbol.addItems(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])
        self._pred_completer = QCompleter(QStringListModel(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]))
        self._pred_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._pred_completer.setCompletionMode(QCompleter.PopupCompletion)
        self._pred_symbol.setCompleter(self._pred_completer)
        self._pred_completer.popup().setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT}; border: 1px solid #2b2f3a;")
        QTimer.singleShot(800, self._load_prediction_symbols)
        top.addWidget(self._pred_symbol)
        top.addWidget(QLabel("Dilim"))
        self._pred_interval = QComboBox()
        self._pred_interval.addItems(["5m", "15m", "1h", "4h"])
        self._pred_interval.setCurrentText("1h")
        top.addWidget(self._pred_interval)
        self._btn_pred = QPushButton("Tahminle")
        self._btn_pred.clicked.connect(self._on_run_prediction)
        top.addWidget(self._btn_pred)
        top.addStretch()
        layout.addLayout(top)
        self._pred_chart = ChartPredictionWidget()
        layout.addWidget(self._pred_chart)
        self._pred_summary = QLabel("Parite ve dilim secip Tahminle'ye tiklayin.")
        self._pred_summary.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(self._pred_summary)
        return w

    def _on_run_prediction(self) -> None:
        symbol = self._pred_symbol.currentText().strip().upper() or "BTCUSDT"
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        interval = self._pred_interval.currentText()
        self._pred_summary.setText("Analiz ediliyor...")
        self._btn_pred.setEnabled(False)

        def run():
            try:
                df = safe_fetch_klines(symbol, interval, 300)
                if getattr(df, "empty", True) or len(df) < 60:
                    return ("error", "Yetersiz veri")
                mtf = run_mtf_analysis(symbol, limit=150)
                mode = "scalp" if interval in ("1m", "5m", "15m") else ("long" if interval == "4h" else "short")
                prev_dir = None
                if (self._last_result and self._last_result.setup
                        and symbol == getattr(self, "_last_analysis_symbol", "")
                        and interval == getattr(self, "_last_analysis_interval", "")):
                    prev_dir = self._last_result.setup.direction
                fng = fetch_fear_greed()
                fear_greed_index = fng["value"] if fng else None
                liq = fetch_liquidations(symbol)
                flow = fetch_exchange_flow_signal()
                res = analyze(df, mtf_consensus=mtf.consensus, min_confidence=4, relax_adx=True,
                             mode=mode, interval=interval, symbol=symbol, prev_direction=prev_dir,
                             fear_greed_index=fear_greed_index, liquidations_24h=liq,
                             exchange_flow_signal=flow)
                if res and res.indicators and res.indicators.get("trend") is None:
                    res.indicators["trend"] = "sideways"
                setup_dir = res.setup.direction if res.setup else None
                trend = (res.indicators.get("trend", "sideways") if res and res.indicators else "sideways")
                a1h = next((a for a in mtf.analyses if a.interval == "1h"), None)
                a4h = next((a for a in mtf.analyses if a.interval == "4h"), None)
                mtf_1h = {"up": "LONG", "down": "SHORT"}.get(a1h.trend, "BEKLE") if a1h else None
                mtf_4h = {"up": "LONG", "down": "SHORT"}.get(a4h.trend, "BEKLE") if a4h else None
                rsi_val = res.indicators.get("rsi") if res and res.indicators else None
                macd_h = res.indicators.get("macd_hist") if res and res.indicators else None
                close_val = res.indicators.get("close") if res and res.indicators else None
                ema50_val = res.indicators.get("ema50") if res and res.indicators else None
                close_above_ema = (close_val > ema50_val) if (close_val is not None and ema50_val is not None) else None
                ind = res.indicators if (res and hasattr(res, "indicators") and isinstance(getattr(res, "indicators", None), dict)) else None
                pred = get_prediction(
                    setup_dir, mtf_1h, mtf_4h, trend,
                    rsi=float(rsi_val) if rsi_val is not None else None,
                    macd_hist=float(macd_h) if macd_h is not None else None,
                    close_vs_ema50=close_above_ema,
                    indicators=ind,
                )
                if res is not None and hasattr(res, "indicators") and isinstance(getattr(res, "indicators", None), dict):
                    raw = res.indicators
                    ind = {}
                    for k in ("rsi", "trend", "support", "resistance", "adx", "close", "atr"):
                        v = raw.get(k)
                        if v is not None and not isinstance(v, (pd.DataFrame, pd.Series)):
                            try:
                                ind[k] = float(v) if isinstance(v, (int, float)) else str(v)
                            except (TypeError, ValueError):
                                ind[k] = str(v)
                o = [float(x) for x in df["open"].values]
                h = [float(x) for x in df["high"].values]
                lo = [float(x) for x in df["low"].values]
                c = [float(x) for x in df["close"].values]
                vol = [float(x) for x in df["volume"].values]
                return (o, h, lo, c, vol, symbol, interval, pred, ind if ind else None, res, df)
            except Exception as e:
                import traceback
                return ("error", f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

        class PredWorker(QThread):
            done = pyqtSignal(object)

            def run(self):
                self.done.emit(run())

        w = PredWorker()
        w.done.connect(self._on_prediction_done)
        w.start()
        self._pred_worker = w

    def _on_prediction_done(self, result) -> None:
        self._btn_pred.setEnabled(True)
        try:
            if isinstance(result, tuple) and result[0] == "error":
                self._pred_summary.setText(f"Hata: {result[1]}")
                return
            o, h, lo, c, vol, symbol, interval, pred, indicators = result[:9]
            res = result[9] if len(result) > 9 else None
            df = result[10] if len(result) > 10 else None
            self._pred_chart.plot_from_arrays(o, h, lo, c, vol, symbol, interval, pred, indicators)
            self._pred_summary.setText(f"Tahmin: {pred.summary}")
            if res and df is not None and res.setup:
                self._df = df
                self._last_result = res
                self._last_analysis_symbol = symbol
                self._last_analysis_interval = interval
                if self._combo_symbol.findText(symbol) >= 0:
                    self._combo_symbol.setCurrentText(symbol)
                if self._combo_interval.findText(interval) >= 0:
                    self._combo_interval.setCurrentText(interval)
                self._chart.plot(self._df, symbol, interval, setup=res.setup)
                self._update_setup_panel()
                self._update_indicator_panel()
                if not (getattr(self, "_filter_high_quality", None) and self._filter_high_quality.isChecked() and not self._passes_quality_filter(res)):
                    self._save_signal_to_db()
                prec = self._price_precision(float(self._df["close"].iloc[-1]))
                self._live_price = float(self._df["close"].iloc[-1])
                self._live_price_label.setText(f"Canli: ${self._live_price:,.{prec}f}")
        except Exception as e:
            self._pred_summary.setText(f"Hata: {e}")

    def _load_prediction_symbols(self) -> None:
        """Tum USDT paritelerini yukle (her coin icin tahmin)."""
        try:
            syms = fetch_usdt_symbols()
            if syms:
                cur = self._pred_symbol.currentText()
                self._pred_symbol.clear()
                self._pred_symbol.addItems(syms)
                if cur and self._pred_symbol.findText(cur) >= 0:
                    self._pred_symbol.setCurrentText(cur)
                elif self._pred_symbol.count() > 0:
                    self._pred_symbol.setCurrentIndex(0)
                model = QStringListModel(syms)
                self._pred_completer.setModel(model)
        except Exception:
            pass

    # --- Scalp tab ---

    def _build_scalp_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        info = QLabel(
            "Scalp modu: 5m/15m dilimlerde kisa vadeli islemler. Dar SL/TP, hizli indikatörler (RSI 7, ATR 7). "
            "Dusuk spreadli pariteleri tercih edin."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Parite"))
        self._scalp_symbol = QComboBox()
        self._scalp_symbol.setEditable(True)
        self._scalp_symbol.setMinimumWidth(140)
        self._scalp_symbol.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        self._scalp_symbol.addItems(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])
        self._scalp_symbol.currentTextChanged.connect(self._on_scalp_symbol_changed)
        row.addWidget(self._scalp_symbol)

        self._scalp_live_price_label = QLabel("Canli: --")
        self._scalp_live_price_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self._scalp_live_price_label.setStyleSheet(f"color: {GREEN};")
        row.addWidget(self._scalp_live_price_label)

        row.addWidget(QLabel("Dilim"))
        self._scalp_interval = QComboBox()
        self._scalp_interval.addItems(["5m", "15m"])
        self._scalp_interval.setCurrentText("15m")
        row.addWidget(self._scalp_interval)

        row.addWidget(QLabel("Mum"))
        self._scalp_limit = QComboBox()
        self._scalp_limit.addItems(["100", "200", "300"])
        self._scalp_limit.setCurrentText("200")
        row.addWidget(self._scalp_limit)

        self._btn_scalp = QPushButton("Scalp Analiz")
        self._btn_scalp.setStyleSheet(f"background-color: {BLUE}; color: white; font-weight: bold; padding: 8px 16px;")
        self._btn_scalp.clicked.connect(self._on_scalp_analyze)
        row.addWidget(self._btn_scalp)
        row.addStretch()
        layout.addLayout(row)

        self._scalp_chart = ChartWidget()
        self._scalp_chart.setMinimumHeight(280)
        self._scalp_chart.setMaximumHeight(380)
        layout.addWidget(self._scalp_chart)

        self._scalp_result_frame = QFrame()
        self._scalp_result_frame.setStyleSheet(f"background-color: {BG_PANEL}; border-radius: 6px; padding: 12px;")
        scalp_result_layout = QVBoxLayout(self._scalp_result_frame)
        self._scalp_signal_label = QLabel("Sinyal: --")
        self._scalp_signal_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        scalp_result_layout.addWidget(self._scalp_signal_label)
        self._scalp_stability_label = QLabel("")
        self._scalp_stability_label.setStyleSheet("color: #6b8e23; font-size: 11px;")
        scalp_result_layout.addWidget(self._scalp_stability_label)
        self._scalp_entry_label = QLabel("Entry / SL / TP: --")
        self._scalp_entry_label.setFont(QFont("Consolas", 11))
        scalp_result_layout.addWidget(self._scalp_entry_label)
        self._scalp_conf_label = QLabel("Guven: --")
        scalp_result_layout.addWidget(self._scalp_conf_label)
        self._scalp_reasons = QLabel("")
        self._scalp_reasons.setWordWrap(True)
        self._scalp_reasons.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        scalp_result_layout.addWidget(self._scalp_reasons)
        layout.addWidget(self._scalp_result_frame)

        self._scalp_indicator_table = QTableWidget(0, 2)
        self._scalp_indicator_table.setHorizontalHeaderLabels(["Indikator", "Deger"])
        self._scalp_indicator_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._scalp_indicator_table.verticalHeader().setDefaultSectionSize(24)
        layout.addWidget(self._scalp_indicator_table)

        return w

    def _on_scalp_analyze(self) -> None:
        symbol = self._scalp_symbol.currentText().strip().upper() or "BTCUSDT"
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        interval = self._scalp_interval.currentText()
        limit = int(self._scalp_limit.currentText())
        self._scalp_analysis_running = True
        self._btn_scalp.setEnabled(False)
        self._scalp_signal_label.setText("Analiz ediliyor...")
        self._scalp_stability_label.setText("")
        self._scalp_entry_label.setText("")
        self._scalp_conf_label.setText("")
        self._scalp_reasons.setText("")

        def run():
            try:
                df = safe_fetch_klines(symbol, interval, limit)
                if df.empty or len(df) < 30:
                    return ("err", "Yetersiz veri")
                mtf = run_mtf_analysis(symbol, limit=150, timeframes=["5m", "15m"])
                try:
                    funding = fetch_funding_rate(symbol)
                except Exception:
                    funding = None
                try:
                    ticker = fetch_ticker_24h(symbol)
                    btc_ticker = fetch_ticker_24h("BTCUSDT")
                    liq_warn = bool(ticker and btc_ticker and btc_ticker.get("quoteVolume", 0) > 0
                                   and ticker.get("quoteVolume", 0) / btc_ticker["quoteVolume"] < 0.01)
                except Exception:
                    liq_warn = False
                ob_data = None
                try:
                    ob_data = fetch_order_book_imbalance(symbol, 20)
                except Exception:
                    pass
                ob_imb = ob_data["imbalance"] if ob_data else None
                spread_bps = ob_data["spread_bps"] if ob_data else None
                calib = get_calibration_stats(symbol, min_evaluated=5, mode="scalp")
                min_conf = calib["calibrated_min"] if calib["total"] >= 15 else 6
                funding_hist = None
                oi = None
                prev_hl = None
                try:
                    funding_hist = fetch_funding_rate_history(symbol, 24)
                except Exception:
                    pass
                try:
                    oi = fetch_open_interest(symbol)
                except Exception:
                    pass
                try:
                    prev_hl = fetch_prev_day_high_low(symbol)
                except Exception:
                    pass
                econ_warn = get_economic_calendar_warning()
                fng = fetch_fear_greed()
                fear_greed_index = fng["value"] if fng else None
                liq = fetch_liquidations(symbol)
                flow = fetch_exchange_flow_signal()
                prev_dir = None
                analyses_in_dir = 0
                if (hasattr(self, "_last_scalp_result") and self._last_scalp_result and self._last_scalp_result.setup
                        and symbol == getattr(self, "_last_scalp_symbol", "") and interval == getattr(self, "_last_scalp_interval", "")):
                    prev_dir = self._last_scalp_result.setup.direction
                    analyses_in_dir = getattr(self, "_scalp_analyses_in_direction", 0)
                res = analyze(
                    df, mtf_consensus=mtf.consensus, funding_rate=funding,
                    min_confidence=min_conf, liquidity_warning=liq_warn,
                    prev_direction=prev_dir, mode="scalp",
                    order_book_imbalance=ob_imb, spread_bps=spread_bps, symbol=symbol,
                    interval=interval, funding_history=funding_hist, open_interest=oi,
                    prev_day_hl=prev_hl, economic_warning=econ_warn,
                    analyses_in_current_direction=analyses_in_dir,
                    fear_greed_index=fear_greed_index, liquidations_24h=liq,
                    exchange_flow_signal=flow,
                )
                return ("ok", res, df, symbol, interval)
            except Exception as e:
                import traceback
                return ("err", f"{e}\n{traceback.format_exc()}")

        class ScalpWorker(QThread):
            done = pyqtSignal(object)
            def run(self):
                self.done.emit(run())

        def on_done(payload):
            self._scalp_analysis_running = False
            try:
                self._btn_scalp.setEnabled(True)
                status = payload[0]
                if status == "err":
                    self._scalp_signal_label.setText(f"Hata: {payload[1]}")
                    self._scalp_signal_label.setStyleSheet(f"color: {RED};")
                    return
                res, df = payload[1], payload[2]
                sym, intv = payload[3], payload[4] if len(payload) > 4 else ("", "")
                prev_sym, prev_intv = self._last_scalp_symbol, self._last_scalp_interval
                self._last_scalp_result = res
                self._last_scalp_symbol = sym
                self._last_scalp_interval = intv
                new_dir = res.setup.direction if res.setup else ""
                if new_dir:
                    if sym != prev_sym or intv != prev_intv:
                        self._scalp_analyses_in_direction = 1
                    elif new_dir == self._scalp_last_direction:
                        self._scalp_analyses_in_direction += 1
                    else:
                        self._scalp_analyses_in_direction = 1
                    self._scalp_last_direction = new_dir
                setup = res.setup
                ind = res.indicators or {}
                prec = self._price_precision(setup.entry)
                self._scalp_signal_label.setText(f"Sinyal: {setup.direction}")
                self._scalp_signal_label.setStyleSheet(
                    f"color: {GREEN};" if setup.direction == "LONG" else f"color: {RED};"
                )
                reasons_text = " | ".join(setup.reasons[:8]) if setup.reasons else ""
                stable = ("(onceki yon korundu)" in reasons_text or "(yon degisimi filtrelendi)" in reasons_text
                         or "(flip kilidi:" in reasons_text)
                self._scalp_stability_label.setText("Yon kararli (kucuk hareketle degismedi)" if stable else "")
                limit_str = f"  Limit: {setup.limit_entry:.{prec}f}" if getattr(setup, "limit_entry", 0) else ""
                self._scalp_entry_label.setText(
                    f"Entry: {setup.entry:.{prec}f}{limit_str}  |  SL: {setup.stop_loss:.{prec}f}  |  "
                    f"TP1: {setup.tp1:.{prec}f}  |  TP2: {setup.tp2:.{prec}f}  |  TP3: {setup.tp3:.{prec}f}"
                )
                self._scalp_conf_label.setText(f"Guven: {setup.confidence}/10  |  RR1: {setup.rr1}x  RR2: {setup.rr2}x  RR3: {setup.rr3}x")
                self._scalp_reasons.setText(reasons_text)

                rows = [
                    ("RSI (7)", f"{ind.get('rsi', '-'):.1f}" if ind.get('rsi') is not None else "-"),
                    ("ATR (7)", f"{ind.get('atr', '-'):.{prec}f}" if ind.get('atr') is not None else "-"),
                    ("VWAP", f"{ind.get('vwap', '-'):.{prec}f}" if ind.get('vwap') is not None else "-"),
                    ("Fiyat vs VWAP", ind.get("price_vs_vwap", "-") or "-"),
                    ("Spread", f"{ind.get('spread_bps', '-'):.1f} bps" if ind.get('spread_bps') is not None else "-"),
                    ("CHoCH", ind.get("market_structure_choch", "-") or "-"),
                    ("Seans", ind.get("session_warning", "-") or "-"),
                ]
                self._scalp_indicator_table.setRowCount(len(rows))
                for i, (name, val) in enumerate(rows):
                    self._scalp_indicator_table.setItem(i, 0, QTableWidgetItem(name))
                    self._scalp_indicator_table.setItem(i, 1, QTableWidgetItem(str(val)))

                self._scalp_chart.plot(df, symbol=sym, interval=intv, setup=setup, scalp=True, figsize=(10, 4))
            except Exception as e:
                self._btn_scalp.setEnabled(True)
                self._scalp_signal_label.setText(f"Hata: {e}")
                self._scalp_signal_label.setStyleSheet(f"color: {RED};")
            finally:
                self._scalp_analysis_running = False

        self._scalp_worker = ScalpWorker()
        self._scalp_worker.done.connect(on_done)
        self._scalp_worker.start()

    def _sync_scalp_symbols(self) -> None:
        """Scalp parite listesini ana listeden guncelle."""
        if hasattr(self, "_combo_symbol") and self._combo_symbol.count() > 0:
            cur = self._scalp_symbol.currentText()
            self._scalp_symbol.clear()
            for i in range(self._combo_symbol.count()):
                self._scalp_symbol.addItem(self._combo_symbol.itemText(i))
            if cur and self._scalp_symbol.findText(cur) >= 0:
                self._scalp_symbol.setCurrentText(cur)

    # --- MTF tab ---

    def _build_mtf_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        top = QHBoxLayout()
        self._btn_mtf = QPushButton("Coklu Analiz Calistir")
        self._btn_mtf.clicked.connect(self._on_run_mtf)
        top.addWidget(self._btn_mtf)
        self._mtf_consensus = QLabel("Sonuc: --")
        self._mtf_consensus.setFont(QFont("Segoe UI", 14, QFont.Bold))
        top.addWidget(self._mtf_consensus)
        top.addStretch()
        layout.addLayout(top)

        self._mtf_table = QTableWidget(0, 7)
        self._mtf_table.setHorizontalHeaderLabels([
            "Zaman Dilimi", "Fiyat", "Trend", "RSI", "MACD Hist", "EMA50 Ustu", "BB Konumu",
        ])
        self._mtf_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._mtf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._mtf_table.verticalHeader().setDefaultSectionSize(26)
        self._mtf_table.setWordWrap(True)
        layout.addWidget(self._mtf_table)
        self._mtf_summary = QLabel("")
        layout.addWidget(self._mtf_summary)
        return w

    # --- History tab ---

    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        top = QHBoxLayout()
        self._btn_load_history = QPushButton("Gecmisi Yukle")
        self._btn_load_history.clicked.connect(self._on_load_history)
        top.addWidget(self._btn_load_history)
        self._btn_update_results = QPushButton("Sonuclari Guncelle")
        self._btn_update_results.clicked.connect(self._on_update_results)
        top.addWidget(QLabel("Yon:"))
        self._history_filter_direction = QComboBox()
        self._history_filter_direction.addItems(["Tumu", "LONG", "SHORT"])
        self._history_filter_direction.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        top.addWidget(self._history_filter_direction)
        top.addWidget(QLabel("Min guc:"))
        self._history_filter_min_strength = QSpinBox()
        self._history_filter_min_strength.setRange(0, 10)
        self._history_filter_min_strength.setValue(0)
        self._history_filter_min_strength.setSpecialValueText("--")
        self._history_filter_min_strength.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        top.addWidget(self._history_filter_min_strength)
        top.addStretch()
        layout.addLayout(top)

        self._history_table = QTableWidget(0, 8)
        self._history_table.setHorizontalHeaderLabels([
            "Tarih", "Parite", "Dilim", "Yon", "Guc",
            "Fiyat", "Sonraki Fiyat", "Sonuc %",
        ])
        self._history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._history_table.verticalHeader().setDefaultSectionSize(26)
        self._history_table.setWordWrap(True)
        layout.addWidget(self._history_table)
        self._stats_label = QLabel("")
        self._stats_label.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self._stats_label)
        return w

    # --- Gercek Islemler tab ---

    def _build_trade_results_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Gerçek işlem sonuçlarınızı girin. Kalibrasyon ve öneriler bu veriye göre iyileşir."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)

        form = QHBoxLayout()
        form.addWidget(QLabel("Parite"))
        self._tr_symbol = QComboBox()
        self._tr_symbol.setEditable(True)
        self._tr_symbol.addItems(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])
        form.addWidget(self._tr_symbol)

        form.addWidget(QLabel("Dilim"))
        self._tr_interval = QComboBox()
        self._tr_interval.addItems(INTERVALS)
        self._tr_interval.setCurrentText("1h")
        form.addWidget(self._tr_interval)

        form.addWidget(QLabel("Yön"))
        self._tr_direction = QComboBox()
        self._tr_direction.addItems(["LONG", "SHORT"])
        form.addWidget(self._tr_direction)

        form.addWidget(QLabel("Giriş"))
        self._tr_entry = QDoubleSpinBox()
        self._tr_entry.setRange(0.0001, 10_000_000)
        self._tr_entry.setDecimals(6)
        form.addWidget(self._tr_entry)
        btn_fill = QPushButton("Fiyat")
        btn_fill.setToolTip("Grafikteki güncel fiyatı girişe yaz")
        btn_fill.clicked.connect(lambda: self._tr_entry.setValue(self._live_price) if self._live_price > 0 else None)
        form.addWidget(btn_fill)

        form.addWidget(QLabel("Çıkış"))
        self._tr_exit = QDoubleSpinBox()
        self._tr_exit.setRange(0.0001, 10_000_000)
        self._tr_exit.setDecimals(6)
        form.addWidget(self._tr_exit)

        form.addWidget(QLabel("Güven (1-10)"))
        self._tr_confidence = QSpinBox()
        self._tr_confidence.setRange(1, 10)
        self._tr_confidence.setValue(7)
        form.addWidget(self._tr_confidence)

        self._btn_add_trade = QPushButton("Ekle")
        self._btn_add_trade.clicked.connect(self._on_add_trade_result)
        form.addWidget(self._btn_add_trade)
        self._btn_delete_trade = QPushButton("Secileni Sil")
        self._btn_delete_trade.clicked.connect(self._on_delete_trade_result)
        form.addWidget(self._btn_delete_trade)
        form.addStretch()
        layout.addLayout(form)

        self._trade_results_table = QTableWidget(0, 8)
        self._trade_results_table.setHorizontalHeaderLabels([
            "ID", "Tarih", "Parite", "Dilim", "Yön", "Giriş", "Çıkış", "Sonuç %",
        ])
        self._trade_results_table.setColumnHidden(0, True)
        self._trade_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._trade_results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._trade_results_table)

        self._trade_results_stats = QLabel("")
        self._trade_results_stats.setStyleSheet(f"color: {GREEN}; font-size: 10px;")
        layout.addWidget(self._trade_results_stats)
        return w

    # --- Paper Trading tab ---

    def _build_paper_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Paper trading: Sinyalleri gercek para kullanmadan test edin. 'Paper\'da Ac' ile sanal pozisyon acilir; "
            "fiyat SL/TP\'ye vurdugunda otomatik kapanir. Canli fiyat guncellemesi icin Grafik sekmesinde WebSocket acin veya Guncelle\'ye tiklayin."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)

        top = QHBoxLayout()
        self._btn_paper_refresh = QPushButton("Fiyatlari Guncelle ve Kapanislari Kontrol Et")
        self._btn_paper_refresh.setStyleSheet(f"background-color: {BLUE}; color: white; padding: 6px;")
        self._btn_paper_refresh.clicked.connect(self._on_paper_refresh)
        top.addWidget(self._btn_paper_refresh)
        top.addStretch()
        self._paper_summary_label = QLabel("Ozet: --")
        self._paper_summary_label.setStyleSheet(f"color: {TEXT}; font-weight: bold;")
        top.addWidget(self._paper_summary_label)
        layout.addLayout(top)

        layout.addWidget(QLabel("Acik pozisyonlar:"))
        self._paper_open_table = QTableWidget(0, 8)
        self._paper_open_table.setHorizontalHeaderLabels([
            "Parite", "Yon", "Entry", "SL", "TP1", "TP2", "TP3", "Pozisyon $",
        ])
        self._paper_open_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._paper_open_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._paper_open_table)

        layout.addWidget(QLabel("Kapanan islemler (son 50):"))
        self._paper_closed_table = QTableWidget(0, 7)
        self._paper_closed_table.setHorizontalHeaderLabels([
            "Tarih", "Parite", "Yon", "Entry", "Cikis", "Neden", "PnL %",
        ])
        self._paper_closed_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._paper_closed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._paper_closed_table)
        return w

    def _on_paper_open(self) -> None:
        res = self._last_result
        if not res or not res.setup:
            QMessageBox.information(self, "Paper", "Once bir sinyal alin (Analiz Et veya Tahminle).")
            return
        setup = res.setup
        symbol = self._last_analysis_symbol or self._combo_symbol.currentText() or "BTCUSDT"
        if paper_has_open(symbol):
            QMessageBox.warning(self, "Paper", f"{symbol} icin zaten acik paper pozisyon var. Once kapanmasini bekleyin.")
            return
        acct = self._get_account_size()
        position_usd = min(max(acct * 0.1, 50), 500)
        pid = paper_open_position(
            symbol=symbol,
            interval=self._last_analysis_interval or self._combo_interval.currentText() or "1h",
            direction=setup.direction,
            entry=setup.entry,
            sl=setup.stop_loss,
            tp1=setup.tp1,
            tp2=setup.tp2,
            tp3=setup.tp3,
            position_usd=position_usd,
            confidence=setup.confidence,
        )
        if pid == -1:
            QMessageBox.warning(self, "Paper", f"{symbol} icin zaten acik pozisyon var.")
            return
        QMessageBox.information(self, "Paper", f"Paper pozisyon acildi: {symbol} {setup.direction} @ {setup.entry:.4f}")
        self._refresh_paper_tables()

    def _on_paper_refresh(self) -> None:
        """Acik pozisyonlar icin guncel fiyat cek, SL/TP kontrol et, tablolari guncelle."""
        positions = get_open_positions()
        for pos in positions:
            try:
                price = fetch_ticker_price(pos.symbol)
                if price is not None:
                    paper_check_close(pos.symbol, float(price))
            except Exception:
                pass
        self._refresh_paper_tables()

    def _refresh_paper_tables(self) -> None:
        if not hasattr(self, "_paper_open_table"):
            return
        open_pos = get_open_positions()
        self._paper_open_table.setRowCount(len(open_pos))
        for i, p in enumerate(open_pos):
            prec = self._price_precision(p.entry_price)
            self._paper_open_table.setItem(i, 0, QTableWidgetItem(p.symbol))
            self._paper_open_table.setItem(i, 1, QTableWidgetItem(p.direction))
            self._paper_open_table.setItem(i, 2, QTableWidgetItem(f"{p.entry_price:.{prec}f}"))
            self._paper_open_table.setItem(i, 3, QTableWidgetItem(f"{p.sl:.{prec}f}"))
            self._paper_open_table.setItem(i, 4, QTableWidgetItem(f"{p.tp1:.{prec}f}"))
            self._paper_open_table.setItem(i, 5, QTableWidgetItem(f"{p.tp2:.{prec}f}"))
            self._paper_open_table.setItem(i, 6, QTableWidgetItem(f"{p.tp3:.{prec}f}"))
            self._paper_open_table.setItem(i, 7, QTableWidgetItem(f"${p.position_usd:.0f}"))
        closed = get_closed_trades(50)
        self._paper_closed_table.setRowCount(len(closed))
        for i, t in enumerate(closed):
            prec = self._price_precision(t.entry_price)
            self._paper_closed_table.setItem(i, 0, QTableWidgetItem(t.exit_time or t.entry_time))
            self._paper_closed_table.setItem(i, 1, QTableWidgetItem(t.symbol))
            self._paper_closed_table.setItem(i, 2, QTableWidgetItem(t.direction))
            self._paper_closed_table.setItem(i, 3, QTableWidgetItem(f"{t.entry_price:.{prec}f}"))
            self._paper_closed_table.setItem(i, 4, QTableWidgetItem(f"{t.exit_price:.{prec}f}" if t.exit_price else "-"))
            self._paper_closed_table.setItem(i, 5, QTableWidgetItem(t.exit_reason or "-"))
            pnl_item = QTableWidgetItem(f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "-")
            if t.pnl_pct is not None:
                pnl_item.setForeground(QColor(GREEN) if t.pnl_pct > 0 else QColor(RED))
            self._paper_closed_table.setItem(i, 6, pnl_item)
        s = get_summary()
        if hasattr(self, "_paper_summary_label"):
            self._paper_summary_label.setText(
                f"Toplam: {s['total_trades']} islem  |  Kazanan: {s['win_count']}  Kaybeden: {s['loss_count']}  |  "
                f"Win rate: %{s['win_rate']}  |  Toplam PnL: %{s['total_pnl_pct']:+.2f}"
            )

    # --- Pozisyon Hesaplayici tab ---

    def _build_position_calc_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Entry, SL ve TP girerek pozisyon buyuklugunu hesaplayin. "
            "Risk % hesaba gore risk $ ve pozisyon $ hesaplanir."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)

        form = QHBoxLayout()
        form.addWidget(QLabel("Entry"))
        self._pc_entry = QDoubleSpinBox()
        self._pc_entry.setRange(0.0001, 10_000_000)
        self._pc_entry.setDecimals(4)
        self._pc_entry.setValue(50000)
        form.addWidget(self._pc_entry)

        form.addWidget(QLabel("Stop-Loss"))
        self._pc_sl = QDoubleSpinBox()
        self._pc_sl.setRange(0.0001, 10_000_000)
        self._pc_sl.setDecimals(4)
        self._pc_sl.setValue(49000)
        form.addWidget(self._pc_sl)

        form.addWidget(QLabel("Take Profit"))
        self._pc_tp = QDoubleSpinBox()
        self._pc_tp.setRange(0.0001, 10_000_000)
        self._pc_tp.setDecimals(4)
        self._pc_tp.setValue(51500)
        form.addWidget(self._pc_tp)

        form.addWidget(QLabel("Yon"))
        self._pc_direction = QComboBox()
        self._pc_direction.addItems(["LONG", "SHORT"])
        form.addWidget(self._pc_direction)

        form.addWidget(QLabel("Hesap ($)"))
        self._pc_account = QSpinBox()
        self._pc_account.setRange(10, 1_000_000)
        self._pc_account.setValue(100)
        form.addWidget(self._pc_account)

        form.addWidget(QLabel("Risk %"))
        self._pc_risk_pct = QDoubleSpinBox()
        self._pc_risk_pct.setRange(0.1, 10.0)
        self._pc_risk_pct.setValue(1.0)
        self._pc_risk_pct.setSingleStep(0.5)
        form.addWidget(self._pc_risk_pct)

        self._btn_pc_calc = QPushButton("Hesapla")
        self._btn_pc_calc.clicked.connect(self._on_position_calc)
        form.addWidget(self._btn_pc_calc)

        btn_sync = QPushButton("Ayarlardan")
        btn_sync.setToolTip("Hesap buyuklugunu sol panelden al")
        btn_sync.clicked.connect(lambda: self._pc_account.setValue(int(self._get_account_size())))
        form.addWidget(btn_sync)
        form.addStretch()
        layout.addLayout(form)

        self._pc_result = QLabel("Sonuc burada gorunecek.")
        self._pc_result.setFont(QFont("Segoe UI", 11))
        self._pc_result.setWordWrap(True)
        self._pc_result.setStyleSheet("padding: 10px; background: #1e232d; border-radius: 6px; color: #eaecef;")
        layout.addWidget(self._pc_result)
        return w

    def _on_position_calc(self) -> None:
        try:
            entry = self._pc_entry.value()
            sl = self._pc_sl.value()
            tp = self._pc_tp.value()
            direction = self._pc_direction.currentText()
            account = self._pc_account.value()
            risk_pct = self._pc_risk_pct.value()

            if entry <= 0:
                self._pc_result.setText("Entry 0'dan buyuk olmali.")
                return

            if direction == "LONG":
                sl_dist_pct = (entry - sl) / entry * 100
            else:
                sl_dist_pct = (sl - entry) / entry * 100

            if sl_dist_pct <= 0:
                self._pc_result.setText("SL, LONG icin entry'nin altinda, SHORT icin ustunde olmali.")
                return

            risk_usd = account * (risk_pct / 100)
            pos_calc = risk_usd / (sl_dist_pct / 100)
            pos_usd = min(pos_calc, account)

            risk_dist = abs(entry - sl)
            profit_dist = abs(tp - entry) if direction == "LONG" else abs(entry - tp)
            rr = profit_dist / risk_dist if risk_dist > 0 else 0

            lines = [
                f"SL mesafesi: %{sl_dist_pct:.2f}",
                f"Risk: ${risk_usd:.2f} (%{risk_pct} hesap)",
                f"Pozisyon buyuklugu: ~${pos_usd:.0f}",
                f"R:R (TP/SL): {rr:.1f}",
            ]
            if pos_calc > account:
                lines.append(f"UYARI: SL cok yakin - risk icin max ${account:.0f} pozisyon")
            self._pc_result.setText("\n".join(lines))
        except Exception as e:
            self._pc_result.setText(f"Hata: {e}")

    # --- Backtest tab ---

    def _build_backtest_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Backtest: Gecmis mum verisi uzerinde stratejinizi simule eder. "
            "SL/TP ATR ile, sinyal gucu ile giris yapar. Sonuc: kac islem, kazanc/kayip, "
            "basari orani, toplam PnL. Gercek para degil; stratejiyi test etmek icin."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px; padding: 4px;")
        layout.addWidget(info)

        params = QHBoxLayout()

        params.addWidget(QLabel("SL (ATR x)"))
        self._spin_sl = QDoubleSpinBox()
        self._spin_sl.setRange(0.5, 5.0)
        self._spin_sl.setValue(1.5)
        self._spin_sl.setSingleStep(0.1)
        params.addWidget(self._spin_sl)

        params.addWidget(QLabel("TP (ATR x)"))
        self._spin_tp = QDoubleSpinBox()
        self._spin_tp.setRange(0.5, 10.0)
        self._spin_tp.setValue(2.5)
        self._spin_tp.setSingleStep(0.1)
        params.addWidget(self._spin_tp)

        params.addWidget(QLabel("Min Sinyal Gucu"))
        self._spin_min_str = QSpinBox()
        self._spin_min_str.setRange(2, 10)
        self._spin_min_str.setValue(4)
        params.addWidget(self._spin_min_str)

        params.addWidget(QLabel("Komisyon %"))
        self._spin_comm = QDoubleSpinBox()
        self._spin_comm.setRange(0.0, 1.0)
        self._spin_comm.setValue(0.1)
        self._spin_comm.setSingleStep(0.01)
        params.addWidget(self._spin_comm)

        self._btn_backtest = QPushButton("Backtest Baslat")
        self._btn_backtest.clicked.connect(self._on_run_backtest)
        params.addWidget(self._btn_backtest)
        self._btn_optimize = QPushButton("Optimize")
        self._btn_optimize.clicked.connect(self._on_optimize_backtest)
        params.addWidget(self._btn_optimize)
        self._btn_symbol_perf = QPushButton("Sembol Performansi")
        self._btn_symbol_perf.clicked.connect(self._on_symbol_performance)
        params.addWidget(self._btn_symbol_perf)
        params.addStretch()
        layout.addLayout(params)

        self._bt_summary = QLabel("Backtest sonuclari burada gosterilecek.")
        self._bt_summary.setFont(QFont("Segoe UI", 10))
        self._bt_summary.setWordWrap(True)
        layout.addWidget(self._bt_summary)

        self._bt_table = QTableWidget(0, 7)
        self._bt_table.setHorizontalHeaderLabels([
            "Giris Zamani", "Yon", "Giris Fiyati",
            "Cikis Zamani", "Cikis Fiyati", "Sonuc %", "Cikis Nedeni",
        ])
        self._bt_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._bt_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._bt_table.verticalHeader().setDefaultSectionSize(26)
        self._bt_table.setWordWrap(True)
        layout.addWidget(self._bt_table)
        return w

    # --- Korelasyon Matrisi tab ---

    def _build_correlation_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Pariteler arasi fiyat korelasyonu. Korelasyon > 0.7 = yuksek - ayni anda pozisyon acmak riski artirir."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)
        btn_row = QHBoxLayout()
        self._btn_corr = QPushButton("Korelasyon Hesapla")
        self._btn_corr.clicked.connect(self._on_correlation_matrix)
        btn_row.addWidget(self._btn_corr)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._corr_summary = QLabel("Sembolleri secip Hesapla'ya tiklayin.")
        self._corr_summary.setWordWrap(True)
        layout.addWidget(self._corr_summary)
        self._corr_table = QTableWidget(0, 4)
        self._corr_table.setHorizontalHeaderLabels(["Parite 1", "Parite 2", "Korelasyon", "Uyari"])
        self._corr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self._corr_table)
        return w

    def _on_correlation_matrix(self) -> None:
        symbols = [self._combo_symbol.itemText(i) for i in range(self._combo_symbol.count())]
        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
        interval = self._combo_interval.currentText()
        self._corr_summary.setText("Hesaplaniyor...")
        self._btn_corr.setEnabled(False)

        def run():
            try:
                r = compute_correlation_matrix(symbols[:8], interval, 100)
                return ("ok", r)
            except Exception as e:
                return ("err", str(e))

        class CorrWorker(QThread):
            done = pyqtSignal(object)

            def run(self):
                self.done.emit(run())

        def on_done(payload):
            try:
                self._btn_corr.setEnabled(True)
                status, data = payload
                if status == "err":
                    self._corr_summary.setText(f"Hata: {data}")
                    return
                r = data
                if not hasattr(r, "matrix") or not hasattr(r, "warning"):
                    self._corr_summary.setText("Beklenmeyen sonuc.")
                    return
                self._corr_summary.setText(r.warning or "Korelasyon hesaplandi.")
                rows = [(k[0], k[1], f"{v:.2f}", "Yuksek!" if v > 0.7 else "") for k, v in (r.matrix or {}).items()]
                self._corr_table.setRowCount(len(rows))
                for i, (s1, s2, c, w) in enumerate(rows):
                    self._corr_table.setItem(i, 0, QTableWidgetItem(str(s1)))
                    self._corr_table.setItem(i, 1, QTableWidgetItem(str(s2)))
                    self._corr_table.setItem(i, 2, QTableWidgetItem(c))
                    wi = QTableWidgetItem(w)
                    if w:
                        wi.setForeground(QColor(ORANGE))
                    self._corr_table.setItem(i, 3, wi)
            except Exception as e:
                self._btn_corr.setEnabled(True)
                self._corr_summary.setText(f"Hata: {e}")

        self._corr_worker = CorrWorker()
        self._corr_worker.done.connect(on_done)
        self._corr_worker.start()

    # --- News / Events tab ---

    def _build_news_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Ekonomiyi ve kripto piyasasini etkileyen olaylar: ekonomik veriler, savaslar, "
            "jeopolitik gerilim, enerji krizi, regulasyon. Bu olaylar oncesi/sonrasi islem acmak riskli olabilir."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px; padding: 4px;")
        layout.addWidget(info)

        self._news_table = QTableWidget(0, 4)
        self._news_table.setHorizontalHeaderLabels(["Olay", "Etki", "Aciklama", "Ne Zaman"])
        self._news_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._news_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._news_table.verticalHeader().setDefaultSectionSize(32)
        layout.addWidget(self._news_table)

        self._populate_news_table()
        return w

    def _populate_news_table(self) -> None:
        events = [
            # Ekonomik
            ("FOMC (Faiz karari)", "Etkiler", "Fed faiz karari - tum piyasalar hareketlenir", "Her 6 haftada bir"),
            ("NFP (Istihdam)", "Etkiler", "ABD is piyasasi verisi - USD ve risk varliklari", "Her ayin ilk Cuma"),
            ("CPI (Enflasyon)", "Etkiler", "Enflasyon verisi - Fed beklentilerini etkiler", "Her ay ortasi"),
            ("PCE (Harcama)", "Etkiler", "Fed'in tercih ettigi enflasyon gostergesi", "Her ay sonu"),
            ("GDP / Resesyon", "Etkiler", "Buyume verisi - risk varliklari etkilenir", "Ceyreklik"),
            # Savas / Jeopolitik
            ("ABD / Savas / Catisma", "Cok Etkiler", "ABD dahil savaslar - risk-off, altin/USD yukselir, kripto dusebilir", "Devam eden"),
            ("Bolgeler arasi savas", "Cok Etkiler", "Ukrayna, Ortadogu vb. - enerji fiyatlari, risk iştahi", "Devam eden"),
            ("Jeopolitik gerilim", "Etkiler", "Ulkeler arasi gerginlik - volatilite artar", "Belirsiz"),
            ("Ekonomik yaptirimlar", "Etkiler", "Uluslararasi yaptirimlar - ticaret, enerji etkilenir", "Belirsiz"),
            ("Enerji krizi", "Etkiler", "Petrol, dogalgaz - enflasyon ve buyumeyi etkiler", "Belirsiz"),
            # Kripto
            ("ETF Onay/Haber", "Etkiler", "Kripto ETF ile ilgili haberler", "Belirsiz"),
            ("Binance/Exchange Haber", "Etkiler", "Buyuk borsa haberleri volatilite yaratir", "Belirsiz"),
            ("Regulasyon haberleri", "Etkiler", "SEC, devlet kararlari - kripto piyasasi", "Belirsiz"),
            # Diger
            ("Gunluk rutin veriler", "Etkilemez", "Kucuk ekonomik veriler genelde etkilemez", "-"),
            ("Kucuk altcoin haber", "Etkilemez", "Tekil coin haberleri BTC'yi az etkiler", "-"),
        ]
        self._news_table.setRowCount(len(events))
        for i, (evt, impact, desc, when) in enumerate(events):
            self._news_table.setItem(i, 0, QTableWidgetItem(evt))
            impact_item = QTableWidgetItem(impact)
            impact_item.setForeground(QColor("#f44336" if impact in ("Etkiler", "Cok Etkiler") else "#4caf50"))
            self._news_table.setItem(i, 1, impact_item)
            self._news_table.setItem(i, 2, QTableWidgetItem(desc))
            self._news_table.setItem(i, 3, QTableWidgetItem(when))

    # --- Oneriler tab ---

    def _build_recommendations_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Binance Futures API - algoritma analizi. LONG/SHORT + Entry, SL, TP, Kaldirac tavsiyesi. "
            "Cift tikla: grafik ve trade setup'a git."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #9e9e9e; font-size: 10px;")
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        self._btn_fetch_recs = QPushButton("Onerileri Getir")
        self._btn_fetch_recs.clicked.connect(self._on_fetch_recommendations)
        btn_row.addWidget(self._btn_fetch_recs)
        self._rec_interval = QComboBox()
        self._rec_interval.addItems(INTERVALS)
        self._rec_interval.setCurrentText("1h")
        btn_row.addWidget(QLabel("Dilim"))
        btn_row.addWidget(self._rec_interval)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rec_table = QTableWidget(0, 11)
        self._rec_table.setHorizontalHeaderLabels([
            "Parite", "Yon", "Entry", "SL", "TP1", "TP2", "TP3", "Kaldirac", "Pozisyon $", "Risk %", "Guc",
        ])
        self._rec_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._rec_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._rec_table.verticalHeader().setDefaultSectionSize(36)
        self._rec_table.setWordWrap(True)
        layout.addWidget(self._rec_table)

        self._rec_detail = QLabel("Detay: Bir satir secin.")
        self._rec_detail.setStyleSheet("color: #9e9e9e; font-size: 10px; padding: 6px;")
        self._rec_detail.setWordWrap(True)
        layout.addWidget(self._rec_detail)
        self._rec_table.itemSelectionChanged.connect(self._on_rec_selection_changed)
        self._rec_table.cellDoubleClicked.connect(self._on_rec_double_clicked)
        return w

    def _on_fetch_recommendations(self) -> None:
        self._btn_fetch_recs.setEnabled(False)
        self._rec_detail.setText("API'den veri cekiliyor... Arka planda calisiyor, bekleyin.")
        interval = self._rec_interval.currentText()

        def run():
            try:
                return get_recommendations(
                    interval=interval,
                    limit=200,
                    max_symbols=6,
                    account=self._get_account_size(),
                )
            except Exception as e:
                return ("error", str(e))

        class RecWorker(QThread):
            finished = pyqtSignal(object)

            def run(self):
                recs = run()
                self.finished.emit(recs)

        self._rec_worker = RecWorker()
        self._rec_worker.finished.connect(self._on_rec_worker_finished)
        self._rec_worker.start()

    def _on_rec_worker_finished(self, result) -> None:
        self._btn_fetch_recs.setEnabled(True)
        if isinstance(result, tuple) and result[0] == "error":
            self._rec_detail.setText(f"Hata: {result[1]}")
            return

        try:
            recs = result
            self._rec_table.setRowCount(len(recs))
            self._rec_data = recs
            for i, r in enumerate(recs):
                prec = self._price_precision(r.entry)
                self._rec_table.setItem(i, 0, QTableWidgetItem(r.symbol))
                dir_item = QTableWidgetItem(r.direction)
                dir_item.setForeground(QColor(_DIRECTION_COLORS.get(r.direction, "#fff")))
                self._rec_table.setItem(i, 1, dir_item)
                self._rec_table.setItem(i, 2, QTableWidgetItem(f"{r.entry:.{prec}f}"))
                self._rec_table.setItem(i, 3, QTableWidgetItem(f"{r.stop_loss:.{prec}f}"))
                self._rec_table.setItem(i, 4, QTableWidgetItem(f"{r.tp1:.{prec}f}"))
                self._rec_table.setItem(i, 5, QTableWidgetItem(f"{r.tp2:.{prec}f}"))
                self._rec_table.setItem(i, 6, QTableWidgetItem(f"{r.tp3:.{prec}f}"))
                self._rec_table.setItem(i, 7, QTableWidgetItem(f"{r.leverage}x"))
                self._rec_table.setItem(i, 8, QTableWidgetItem(f"${r.pos_usd:.0f}"))
                self._rec_table.setItem(i, 9, QTableWidgetItem(f"%{r.risk_pct:.1f}"))
                self._rec_table.setItem(i, 10, QTableWidgetItem(f"{r.confidence}/10"))
            if recs:
                self._rec_detail.setText(f"{len(recs)} coin - Entry/SL/TP/Kaldirac tavsiyesi. Cift tikla: grafik ve trade setup.")
            else:
                self._rec_detail.setText("LONG/SHORT setup bulunamadi. Tekrar deneyin veya farkli dilim secin.")
        except Exception as e:
            self._rec_detail.setText(f"Hata: {e}")

    def _on_rec_selection_changed(self) -> None:
        row = self._rec_table.currentRow()
        if row < 0 or not hasattr(self, "_rec_data") or row >= len(self._rec_data):
            return
        r = self._rec_data[row]
        prec = self._price_precision(r.entry)
        limit_str = f"  Limit: {r.limit_entry:.{prec}f}" if getattr(r, "limit_entry", 0) else ""
        lines = [
            f"{r.symbol} | {r.direction} | Guc {r.confidence}/10",
            f"Entry: {r.entry:.{prec}f}{limit_str}  SL: {r.stop_loss:.{prec}f}",
            f"TP1: {r.tp1:.{prec}f}  TP2: {r.tp2:.{prec}f}  TP3: {r.tp3:.{prec}f}",
            f"Kaldirac: {r.leverage}x  Pozisyon: ${r.pos_usd:.0f}  Risk: %{r.risk_pct:.1f}",
            r.reason,
        ]
        self._rec_detail.setText("\n".join(lines))

    def _on_rec_double_clicked(self, row: int, _col: int) -> None:
        """Cift tiklayinca o coin grafik/trade setup'a yuklenir."""
        if row < 0 or not hasattr(self, "_rec_data") or row >= len(self._rec_data):
            return
        r = self._rec_data[row]
        sym = r.symbol
        if self._combo_symbol.findText(sym) == -1:
            self._combo_symbol.addItem(sym)
        self._combo_symbol.setCurrentText(sym)
        self._combo_interval.setCurrentText(self._rec_interval.currentText())
        self._tabs.setCurrentIndex(0)
        self._on_refresh()

    # --- Rapor tab ---

    def _build_report_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        btn_row = QHBoxLayout()
        self._btn_report_weekly = QPushButton("Haftalik Rapor")
        self._btn_report_weekly.clicked.connect(lambda: self._on_show_report("weekly"))
        btn_row.addWidget(self._btn_report_weekly)
        self._btn_report_monthly = QPushButton("Aylik Rapor")
        self._btn_report_monthly.clicked.connect(lambda: self._on_show_report("monthly"))
        btn_row.addWidget(self._btn_report_monthly)
        self._btn_export_csv = QPushButton("CSV Export")
        self._btn_export_csv.clicked.connect(lambda: self._on_export_report("csv"))
        btn_row.addWidget(self._btn_export_csv)
        self._btn_export_excel = QPushButton("Excel Export")
        self._btn_export_excel.clicked.connect(lambda: self._on_export_report("excel"))
        btn_row.addWidget(self._btn_export_excel)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._report_text = QLabel("Rapor gostermek icin Haftalik veya Aylik tiklayin.")
        self._report_text.setWordWrap(True)
        self._report_text.setStyleSheet("font-family: Consolas; font-size: 11px; padding: 12px;")
        self._report_text.setTextFormat(Qt.RichText)
        layout.addWidget(self._report_text)
        grp_risk = QGroupBox("Risk Ozeti")
        risk_layout = QVBoxLayout(grp_risk)
        self._risk_max_pos_label = QLabel("Maks eszamanli pozisyon: 3 onerilir")
        self._risk_corr_label = QLabel("Korelasyon: BTC/ETH/SOL ayni anda hepsinde pozisyon acmayin.")
        self._risk_drawdown_label = QLabel("")
        self._update_risk_drawdown_label()
        risk_layout.addWidget(self._risk_max_pos_label)
        risk_layout.addWidget(self._risk_corr_label)
        risk_layout.addWidget(self._risk_drawdown_label)
        layout.addWidget(grp_risk)
        return w

    def _on_show_report(self, period: str) -> None:
        try:
            txt = generate_report_text(period)
            self._report_text.setText(txt.replace("\n", "<br>"))
            if hasattr(self, "_risk_drawdown_label"):
                self._update_risk_drawdown_label()
        except Exception as e:
            self._report_text.setText(f"Hata: {e}")

    def _on_export_report(self, fmt: str) -> None:
        try:
            records = get_history_filtered(limit=500)
            if not records:
                self._status_label.setText("Export edilecek kayit yok.")
                return
            ext = ".csv" if fmt == "csv" else ".xlsx"
            path, _ = QFileDialog.getSaveFileName(self, "Rapor Kaydet", f"rapor_{datetime.now().strftime('%Y%m%d')}{ext}", f"{fmt.upper()} (*{ext})")
            if path:
                ok = export_to_csv(records, Path(path)) if fmt == "csv" else export_to_excel(records, Path(path))
                self._status_label.setText(f"Kaydedildi: {path}" if ok else "Export hatasi")
        except Exception as e:
            self._status_label.setText(f"Hata: {e}")

    def _update_risk_drawdown_label(self) -> None:
        try:
            trades = get_trade_results(limit=10)
            if len(trades) >= 3:
                pnls = [getattr(t, "result_pct", 0) or 0 for t in trades]
                total = sum(pnls)
                if total < -5:
                    self._risk_drawdown_label.setText(f"UYARI: Son 10 islemde toplam %{total:.1f} kayip - dikkat!")
                    self._risk_drawdown_label.setStyleSheet("color: #f44336;")
                else:
                    self._risk_drawdown_label.setText(f"Son 10 islem toplam: %{total:.1f}")
                    self._risk_drawdown_label.setStyleSheet("")
            else:
                self._risk_drawdown_label.setText("Drawdown: Son 10 islemde toplam kayip %5+ ise dikkat.")
        except Exception:
            self._risk_drawdown_label.setText("Drawdown: Son 10 islemde toplam kayip %5+ ise dikkat.")

    # --- Right panel: Trade Setup ---

    def _build_right_panel(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        grp = QGroupBox("Trade Setup")
        grp.setMinimumWidth(320)
        grp.setMaximumWidth(420)
        grp_layout = QVBoxLayout(grp)

        self._selected_symbol_label = QLabel("Seçili parite: --")
        self._selected_symbol_label.setStyleSheet(f"color: {BLUE}; font-size: 10px;")
        self._selected_symbol_label.setAlignment(Qt.AlignCenter)
        grp_layout.addWidget(self._selected_symbol_label)

        self._dir_label = QLabel("--")
        self._dir_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
        self._dir_label.setAlignment(Qt.AlignCenter)
        self._dir_label.setWordWrap(True)
        grp_layout.addWidget(self._dir_label)

        self._confidence_label = QLabel("Guven: --")
        self._confidence_label.setFont(QFont("Segoe UI", 11))
        self._confidence_label.setAlignment(Qt.AlignCenter)
        self._confidence_label.setWordWrap(True)
        self._confidence_label.setMinimumHeight(36)
        grp_layout.addWidget(self._confidence_label)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        grp_layout.addWidget(line)

        self._setup_table = QTableWidget(7, 2)
        self._setup_table.setHorizontalHeaderLabels(["", "Fiyat"])
        self._setup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._setup_table.verticalHeader().setVisible(False)
        self._setup_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._setup_table.verticalHeader().setDefaultSectionSize(28)
        self._setup_table.setWordWrap(True)
        self._setup_table.setStyleSheet("QTableWidget::item { padding: 6px; min-height: 24px; }")
        grp_layout.addWidget(self._setup_table)

        self._sl_rule_label = QLabel("")
        self._sl_rule_label.setStyleSheet("color: #9e9e9e; font-size: 9px;")
        self._sl_rule_label.setWordWrap(True)
        grp_layout.addWidget(self._sl_rule_label)

        grp_layout.addWidget(QLabel("Nedenler:"))
        self._reason_table = QTableWidget(0, 1)
        self._reason_table.setHorizontalHeaderLabels(["Aciklama"])
        self._reason_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._reason_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._reason_table.verticalHeader().setDefaultSectionSize(26)
        self._reason_table.setWordWrap(True)
        self._reason_table.setStyleSheet("QTableWidget::item { padding: 5px; min-height: 22px; }")
        grp_layout.addWidget(self._reason_table)

        self._btn_paper_open = QPushButton("Paper'da Ac")
        self._btn_paper_open.setStyleSheet(f"background-color: {ORANGE}; color: white; font-weight: bold; padding: 6px;")
        self._btn_paper_open.setToolTip("Mevcut sinyali sanal (paper) pozisyon olarak acar. Gercek para kullanilmaz.")
        self._btn_paper_open.clicked.connect(self._on_paper_open)
        grp_layout.addWidget(self._btn_paper_open)

        layout.addWidget(grp)

        grp_filter = QGroupBox("Sinyal Filtresi")
        grp_filter.setStyleSheet(f"font-size: 10px;")
        flay = QVBoxLayout(grp_filter)
        self._filter_high_quality = QCheckBox("Sadece yuksek kalite sinyaller")
        self._filter_high_quality.setToolTip("Acikken sadece min guven ve confluence gecen sinyaller gosterilir.")
        self._filter_high_quality.setChecked(False)
        flay.addWidget(self._filter_high_quality)
        row_f = QHBoxLayout()
        row_f.addWidget(QLabel("Min guven:"))
        self._spin_min_quality = QSpinBox()
        self._spin_min_quality.setRange(5, 10)
        self._spin_min_quality.setValue(7)
        self._spin_min_quality.setStyleSheet(f"background-color: {BG_PANEL}; color: {TEXT};")
        row_f.addWidget(self._spin_min_quality)
        self._filter_require_confluence = QCheckBox("Confluence gecmeli")
        self._filter_require_confluence.setChecked(True)
        row_f.addWidget(self._filter_require_confluence)
        row_f.addStretch()
        flay.addLayout(row_f)
        self._filter_reject_label = QLabel("")
        self._filter_reject_label.setStyleSheet("color: #ff9800; font-size: 9px;")
        self._filter_reject_label.setWordWrap(True)
        flay.addWidget(self._filter_reject_label)
        layout.addWidget(grp_filter)

        grp2 = QGroupBox("Indikator Ozeti")
        grp2.setMinimumWidth(320)
        grp2.setMaximumWidth(420)
        grp2_layout = QVBoxLayout(grp2)
        self._indicator_table = QTableWidget(0, 2)
        self._indicator_table.setHorizontalHeaderLabels(["Indikator", "Deger"])
        self._indicator_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._indicator_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._indicator_table.verticalHeader().setDefaultSectionSize(24)
        self._indicator_table.setWordWrap(True)
        self._indicator_table.setStyleSheet("QTableWidget::item { padding: 4px; min-height: 20px; }")
        grp2_layout.addWidget(self._indicator_table)
        layout.addWidget(grp2)

        grp3 = QGroupBox("Confluence Matrisi (10 Kriter)")
        grp3.setMinimumWidth(320)
        grp3.setMaximumWidth(420)
        grp3_layout = QVBoxLayout(grp3)
        self._confluence_table = QTableWidget(0, 3)
        self._confluence_table.setHorizontalHeaderLabels(["Kriter", "OK", "Detay"])
        self._confluence_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._confluence_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._confluence_table.verticalHeader().setDefaultSectionSize(22)
        self._confluence_table.setStyleSheet("QTableWidget::item { padding: 3px; min-height: 18px; }")
        grp3_layout.addWidget(self._confluence_table)
        self._confluence_score_label = QLabel("Puan: --/10 (min 6)")
        self._confluence_score_label.setStyleSheet(f"color: {GREEN}; font-size: 10px;")
        grp3_layout.addWidget(self._confluence_score_label)
        layout.addWidget(grp3)

        layout.addStretch()
        return layout

    # ==================================================================
    # Symbol search
    # ==================================================================

    def _load_symbols(self) -> None:
        try:
            symbols = fetch_usdt_symbols()
        except Exception:
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

        model = QStringListModel(symbols)
        self._search_completer.setModel(model)

        self._combo_symbol.clear()
        self._combo_symbol.setEditable(True)
        fav_str = QSettings("BinanceTA", "TeknikAnaliz").value("favorite_symbols", "", type=str) or ""
        fav = [x.strip() for x in fav_str.split(",") if x.strip() and x.strip() in symbols]
        top = fav if fav else [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
            "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT",
            "PEPEUSDT", "SHIBUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
        ]
        for s in top:
            if s in symbols and self._combo_symbol.findText(s) == -1:
                self._combo_symbol.addItem(s)
        if hasattr(self, "_scalp_symbol"):
            self._sync_scalp_symbols()

    def _on_interval_changed(self, interval: str) -> None:
        if interval in ("1m", "5m"):
            self._interval_warning_label.setText("UYARI: 1m/5m gurultulu - 15m/1h/4h tercih edin")
        else:
            self._interval_warning_label.setText("")

    def _on_symbol_changed(self, symbol: str) -> None:
        if not symbol:
            return
        if hasattr(self, "_selected_symbol_label"):
            self._selected_symbol_label.setText(f"Seçili parite: {symbol}")
        if hasattr(self, "_price_timer") and self._price_timer and self._price_timer.isActive():
            self._price_timer.stop()
        self._price_timer = QTimer(self)
        self._price_timer.setSingleShot(True)
        self._price_timer.timeout.connect(self._fetch_price_for_current_symbol)
        self._price_timer.start(400)

    def _fetch_price_for_current_symbol(self) -> None:
        symbol = self._combo_symbol.currentText()
        if not symbol:
            return

        class PriceWorker(QThread):
            done = pyqtSignal(str, object)

            def __init__(self, sym):
                super().__init__()
                self.sym = sym

            def run(self):
                try:
                    p = fetch_ticker_price(self.sym)
                    self.done.emit(self.sym, p)
                except Exception:
                    self.done.emit(self.sym, None)

        w = PriceWorker(symbol)
        w.done.connect(self._on_price_fetched)
        w.start()
        self._price_worker = w

    def _on_price_fetched(self, symbol: str, price) -> None:
        if price is not None and self._combo_symbol.currentText() == symbol:
            self._live_price = price
            prec = self._price_precision(price)
            self._live_price_label.setText(f"Canli: ${price:,.{prec}f}")
            self._check_price_match_and_refresh()

    def _on_min_conf_changed(self, value: int) -> None:
        s = QSettings("BinanceTA", "TeknikAnaliz")
        s.setValue("min_confidence", value)

    def _on_theme_changed(self, idx: int) -> None:
        from PyQt5.QtWidgets import QApplication
        from theme import global_stylesheet
        dark = idx == 0
        QSettings("BinanceTA", "TeknikAnaliz").setValue("theme_dark", dark)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(global_stylesheet(dark=dark))
        self._status_label.setText("Tema degistirildi. Tam uygulama icin yeniden baslatin.")

    def _on_add_favorite(self) -> None:
        sym = self._combo_symbol.currentText()
        if not sym:
            return
        s = QSettings("BinanceTA", "TeknikAnaliz")
        fav_str = s.value("favorite_symbols", "", type=str) or ""
        fav = [x.strip() for x in fav_str.split(",") if x.strip()]
        if sym not in fav:
            fav.append(sym)
            fav = fav[-15:]
            s.setValue("favorite_symbols", ",".join(fav))
            self._status_label.setText(f"Favorilere eklendi: {sym}")
        else:
            self._status_label.setText(f"{sym} zaten favorilerde")

    def _on_account_size_changed(self, value: int) -> None:
        QSettings("BinanceTA", "TeknikAnaliz").setValue("account_size", value)

    def _get_account_size(self) -> float:
        return float(self._spin_account_size.value())

    def _on_search_text_changed(self, text: str) -> None:
        results = search_symbols(text)
        model = QStringListModel(results)
        self._search_completer.setModel(model)

    def _on_search_enter(self) -> None:
        text = self._search_input.text().strip().upper()
        if not text:
            return

        if not text.endswith("USDT"):
            text += "USDT"

        idx = self._combo_symbol.findText(text)
        if idx == -1:
            self._combo_symbol.addItem(text)
            self._combo_symbol.setCurrentText(text)
        else:
            self._combo_symbol.setCurrentIndex(idx)

        self._search_input.clear()
        self._on_refresh()

    # ==================================================================
    # Timer
    # ==================================================================

    def _setup_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_refresh)
        self._timer.start(15_000)

    def _setup_market_context_timer(self) -> None:
        self._market_timer = QTimer(self)
        self._market_timer.timeout.connect(self._update_market_context)
        self._market_timer.start(300_000)
        self._update_market_context()

    def _setup_scalp_timers(self) -> None:
        """Scalp sekmesinde canli fiyat (5 sn) ve analiz (30 sn) timerlari."""
        self._scalp_price_timer = QTimer(self)
        self._scalp_price_timer.timeout.connect(self._scalp_fetch_price)
        self._scalp_analysis_timer = QTimer(self)
        self._scalp_analysis_timer.timeout.connect(self._scalp_timer_analysis)

    def _scalp_fetch_price(self) -> None:
        """Scalp paritesinin canli fiyatini cek."""
        try:
            if self._tabs.currentIndex() < 0 or self._tabs.tabText(self._tabs.currentIndex()) != "Scalp":
                return
        except Exception:
            return
        symbol = self._scalp_symbol.currentText().strip().upper() or "BTCUSDT"
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        class ScalpPriceWorker(QThread):
            done = pyqtSignal(str, object)
            def __init__(self, sym):
                super().__init__()
                self.sym = sym
            def run(self):
                try:
                    p = fetch_ticker_price(self.sym)
                    self.done.emit(self.sym, p)
                except Exception:
                    self.done.emit(self.sym, None)

        self._scalp_price_worker = ScalpPriceWorker(symbol)
        self._scalp_price_worker.done.connect(self._on_scalp_price_fetched)
        self._scalp_price_worker.start()

    def _on_scalp_price_fetched(self, symbol: str, price) -> None:
        try:
            if price is None:
                return
            if self._tabs.currentIndex() < 0 or self._tabs.tabText(self._tabs.currentIndex()) != "Scalp":
                return
            cur = self._scalp_symbol.currentText().strip().upper() or "BTCUSDT"
            if not cur.endswith("USDT"):
                cur += "USDT"
            if cur == symbol:
                prec = self._price_precision(price)
                self._scalp_live_price_label.setText(f"Canli: ${price:,.{prec}f}")
        except Exception:
            pass

    def _scalp_timer_analysis(self) -> None:
        """Timer ile scalp analizi - zaten calisiyorsa atla."""
        try:
            if self._tabs.currentIndex() < 0 or self._tabs.tabText(self._tabs.currentIndex()) != "Scalp":
                return
            if self._scalp_analysis_running:
                return
            self._on_scalp_analyze()
        except Exception:
            pass

    def _on_scalp_symbol_changed(self, _text: str) -> None:
        """Scalp paritesi degistiginde fiyati hemen cek."""
        try:
            if self._tabs.currentIndex() >= 0 and self._tabs.tabText(self._tabs.currentIndex()) == "Scalp":
                self._scalp_fetch_price()
        except Exception:
            pass

    def _fear_greed_comment(self, value: int) -> str:
        """Fear & Greed degerine gore yorum."""
        if value <= 24:
            return "Asiri korku - LONG icin potansiyel firsat, dikkatli kullan"
        if value <= 44:
            return "Korku - LONG oncelikli, risk yonet"
        if value <= 55:
            return "Notr - Normal islem"
        if value <= 75:
            return "Acgozluluk - Dikkatli ol, LONG'ta erken kar al"
        return "Asiri acgozluluk - Duzeltme riski yuksek, SHORT dusun"

    def _update_market_context(self) -> None:
        import time
        now = time.time()
        if self._market_context_ts > 0 and now - self._market_context_ts < 120:
            return
        self._market_context_ts = now
        try:
            fng = fetch_fear_greed()
            btc_dom = fetch_btc_dominance()
            parts = []
            if fng:
                v = fng["value"]
                parts.append(f"Fear & Greed: {v} ({fng['classification']})")
            else:
                parts.append("Fear & Greed: --")
            if btc_dom is not None:
                parts.append(f"BTC Dom: %{btc_dom:.1f}")
            else:
                parts.append("BTC Dom: --")
            try:
                sym = self._combo_symbol.currentText() or "BTCUSDT"
                sess = get_session_win_rates(sym)
                cur = sess.get("current", "")
                if cur and sess.get(cur, {}).get("trades", 0) >= 5:
                    wr = sess[cur]["win_rate"]
                    name = {"asia": "Asia", "london": "London", "ny": "NY"}.get(cur, cur)
                    parts.append(f"Seans {name}: Win %{wr}")
            except Exception:
                pass
            self._market_context_label.setText(" | ".join(parts))
            if fng:
                comment = self._fear_greed_comment(fng["value"])
                self._market_context_comment.setText(comment)
            else:
                self._market_context_comment.setText("")
        except Exception:
            self._market_context_label.setText("Fear & Greed: -- | BTC Dom: --")
            self._market_context_comment.setText("")

    # ==================================================================
    # REST data refresh + analysis (arka planda - donma yok)
    # ==================================================================

    def _on_refresh(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or ""
            interval = self._combo_interval.currentText()
            limit = int(self._combo_limit.currentText())
        except Exception as e:
            self._status_label.setText(f"Hata: {e}")
            return

        if not symbol:
            return

        self._status_label.setText(f"Analiz ediliyor... {symbol} {interval}")
        self._btn_refresh.setEnabled(False)

        prev_dir = None
        analyses_in_dir = 0
        if (self._last_result and self._last_result.setup
                and symbol == self._last_analysis_symbol and interval == self._last_analysis_interval):
            prev_dir = self._last_result.setup.direction
            analyses_in_dir = getattr(self, "_main_analyses_in_direction", 0)
        mode_idx = self._combo_mode.currentIndex()
        mode = "long" if mode_idx == 1 else ("scalp" if mode_idx == 2 else "short")

        class AnalysisWorker(QThread):
            done = pyqtSignal(object)

            def __init__(self, sym, intv, lim, min_conf_val, prev_direction, analysis_mode, analyses_in_direction):
                super().__init__()
                self.sym = sym
                self.intv = intv
                self.lim = lim
                self.min_conf_val = min_conf_val
                self.prev_direction = prev_direction
                self.analysis_mode = analysis_mode
                self.analyses_in_direction = analyses_in_direction

            def run(self):
                try:
                    df = safe_fetch_klines(self.sym, self.intv, self.lim)
                    if df.empty:
                        self.done.emit({"error": "Veri alinamadi"})
                        return
                    mtf_consensus = None
                    try:
                        mtf_tfs = ["5m", "15m"] if self.analysis_mode == "scalp" else None
                        mtf = run_mtf_analysis(self.sym, limit=150, timeframes=mtf_tfs)
                        mtf_consensus = mtf.consensus
                    except Exception:
                        pass
                    funding_rate = None
                    try:
                        funding_rate = fetch_funding_rate(self.sym)
                    except Exception:
                        pass
                    liquidity_warning = False
                    try:
                        ticker = fetch_ticker_24h(self.sym)
                        btc_ticker = fetch_ticker_24h("BTCUSDT")
                        if ticker and btc_ticker and btc_ticker.get("quoteVolume", 0) > 0:
                            if ticker.get("quoteVolume", 0) / btc_ticker["quoteVolume"] < 0.01:
                                liquidity_warning = True
                    except Exception:
                        pass
                    ob_data = None
                    try:
                        ob_data = fetch_order_book_imbalance(self.sym, 20)
                    except Exception:
                        pass
                    min_calib = 5 if self.analysis_mode == "scalp" else 15
                    calib = get_calibration_stats(self.sym, min_evaluated=min_calib, mode=self.analysis_mode)
                    min_conf = calib["calibrated_min"] if calib["total"] >= 15 else self.min_conf_val
                    min_conf = max(min_conf, self.min_conf_val)
                    ob_imb = ob_data["imbalance"] if ob_data else None
                    spread_bps = ob_data["spread_bps"] if ob_data else None
                    funding_hist = None
                    oi = None
                    prev_hl = None
                    try:
                        funding_hist = fetch_funding_rate_history(self.sym, 24)
                    except Exception:
                        pass
                    try:
                        oi = fetch_open_interest(self.sym)
                    except Exception:
                        pass
                    try:
                        prev_hl = fetch_prev_day_high_low(self.sym)
                    except Exception:
                        pass
                    econ_warn = get_economic_calendar_warning()
                    fng = fetch_fear_greed()
                    fear_greed_index = fng["value"] if fng else None
                    liq = fetch_liquidations(self.sym)
                    flow = fetch_exchange_flow_signal()
                    result = analyze(
                        df, mtf_consensus=mtf_consensus, funding_rate=funding_rate,
                        min_confidence=min_conf, liquidity_warning=liquidity_warning,
                        prev_direction=self.prev_direction, mode=self.analysis_mode,
                        order_book_imbalance=ob_imb, spread_bps=spread_bps, symbol=self.sym,
                        interval=self.intv,
                        funding_history=funding_hist, open_interest=oi,
                        prev_day_hl=prev_hl, economic_warning=econ_warn,
                        analyses_in_current_direction=self.analyses_in_direction,
                        fear_greed_index=fear_greed_index, liquidations_24h=liq,
                        exchange_flow_signal=flow,
                    )
                    self.done.emit({
                        "df": df, "result": result, "mtf_consensus": mtf_consensus,
                        "calib": calib, "symbol": self.sym, "interval": self.intv,
                        "mode": self.analysis_mode,
                    })
                except Exception as e:
                    self.done.emit({"error": str(e)})

        w = AnalysisWorker(symbol, interval, limit, self._spin_min_conf.value(), prev_dir, mode, analyses_in_dir)
        w.done.connect(self._on_analysis_done)
        w.start()
        self._analysis_worker = w

    def _on_analysis_done(self, data: dict) -> None:
        self._btn_refresh.setEnabled(True)
        if "error" in data:
            self._status_label.setText(f"Hata: {data['error']}")
            return

        try:
            self._df = data["df"]
            self._last_result = data["result"]
            symbol = data["symbol"]
            interval = data["interval"]
            prev_sym, prev_intv = self._last_analysis_symbol, self._last_analysis_interval
            self._last_analysis_symbol = symbol
            self._last_analysis_interval = interval
            if data.get("mode") == "scalp" and self._last_result and self._last_result.setup:
                new_dir = self._last_result.setup.direction
                if symbol != prev_sym or interval != prev_intv:
                    self._main_analyses_in_direction = 1
                elif new_dir == self._main_last_direction:
                    self._main_analyses_in_direction += 1
                else:
                    self._main_analyses_in_direction = 1
                self._main_last_direction = new_dir
            mtf_consensus = data["mtf_consensus"]
            calib = data["calib"]

            self._mtf_consensus.setText(f"MTF: {mtf_consensus}" if mtf_consensus else "MTF: --")
            self._mtf_consensus.setStyleSheet(f"color: {_DIRECTION_COLORS.get(mtf_consensus, '#9e9e9e')};")

            if calib["total"] >= 15:
                band_str = " ".join(f"{k}:%{v}" for k, v in calib.get("by_band", {}).items())
                tr = calib.get("total_trades", 0)
                extra = f" + {tr} gerçek işlem" if tr else ""
                self._calib_label.setText(f"Kalibre: min={calib['calibrated_min']} ({calib['total']} sinyal{extra}) {band_str}")
            else:
                self._calib_label.setText("Kalibre: yetersiz veri")

            self._chart.plot(self._df, symbol, interval, setup=self._last_result.setup)

            last_close = float(self._df["close"].iloc[-1])
            self._live_price = last_close
            prec = self._price_precision(last_close)
            self._live_price_label.setText(f"Canli: ${last_close:,.{prec}f}")

            self._update_setup_panel()
            self._update_indicator_panel()
            if not (getattr(self, "_filter_high_quality", None) and self._filter_high_quality.isChecked() and self._last_result and self._last_result.setup and not self._passes_quality_filter(self._last_result)):
                self._save_signal_to_db()
                self._maybe_notify_setup(symbol, interval)

            now = datetime.now().strftime("%H:%M:%S")
            self._status_label.setText(f"Son guncelleme: {now}  |  {symbol} {interval}  |  {len(self._df)} mum")
        except Exception as e:
            self._status_label.setText(f"Hata: {e}")

    # ==================================================================
    # WebSocket
    # ==================================================================

    def _toggle_ws(self) -> None:
        if self._ws.is_running:
            self._ws.disconnect()
            self._btn_ws.setText("WebSocket Baslat")
            self._ws_status_label.setText("WS: Kapalı")
            self._ws_status_label.setStyleSheet(f"color: {ORANGE};")
        else:
            symbol = self._combo_symbol.currentText()
            interval = self._combo_interval.currentText()
            self._ws.connect(
                symbol, interval,
                on_kline=lambda d: self._ws_bridge.kline_received.emit(d),
                on_ticker=lambda d: self._ws_bridge.ticker_received.emit(d),
                on_status=lambda s: self._ws_bridge.status_received.emit(s),
            )
            self._btn_ws.setText("WebSocket Durdur")

    def _on_ws_kline(self, data: dict) -> None:
        if self._df.empty:
            return
        last_idx = self._df.index[-1]
        self._df.at[last_idx, "close"] = data["close"]
        self._df.at[last_idx, "high"] = max(self._df.at[last_idx, "high"], data["high"])
        self._df.at[last_idx, "low"] = min(self._df.at[last_idx, "low"], data["low"])
        self._df.at[last_idx, "volume"] = data["volume"]
        if data["is_closed"]:
            self._on_refresh()

    def _on_ws_ticker(self, data: dict) -> None:
        self._live_price = data["close"]
        prec = self._price_precision(self._live_price)
        self._live_price_label.setText(f"Canli: ${data['close']:,.{prec}f}")
        sym = self._combo_symbol.currentText()
        if sym:
            try:
                paper_check_close(sym, float(self._live_price))
                self._refresh_paper_tables()
            except Exception:
                pass
        self._check_price_match_and_refresh()

    def _on_ws_status(self, msg: str) -> None:
        self._ws_status_label.setText(f"WS: {msg}")
        if "Baglandi" in msg:
            self._ws_status_label.setStyleSheet(f"color: {GREEN};")
        elif "Hata" in msg or "kesildi" in msg:
            self._ws_status_label.setStyleSheet(f"color: {RED};")

    def _check_price_match_and_refresh(self) -> None:
        """Canli fiyat wait_for_long/short'a yaklasinca analizi yenile."""
        res = self._last_result
        if not res or res.setup is not None:
            return
        live = self._live_price
        if live <= 0:
            return
        tol_pct = 0.002
        wl, ws = res.wait_for_long, res.wait_for_short
        if getattr(self, "_price_match_refreshing", False):
            return
        if wl is not None and abs(live - wl) / wl <= tol_pct:
            self._price_match_refreshing = True
            self._on_refresh()
            QTimer.singleShot(5000, self._reset_price_match_flag)
        elif ws is not None and abs(live - ws) / ws <= tol_pct:
            self._price_match_refreshing = True
            self._on_refresh()
            QTimer.singleShot(5000, self._reset_price_match_flag)

    def _reset_price_match_flag(self) -> None:
        self._price_match_refreshing = False

    # ==================================================================
    # Trade Setup panel
    # ==================================================================

    def _passes_quality_filter(self, res: AnalysisResult) -> bool:
        """Sadece yuksek kalite filtresi: min guven + confluence (seciliyse)."""
        if not res or not res.setup:
            return False
        if not getattr(self, "_filter_high_quality", None) or not self._filter_high_quality.isChecked():
            return True
        min_q = getattr(self, "_spin_min_quality", None)
        min_conf = min_q.value() if min_q else 7
        if res.setup.confidence < min_conf:
            return False
        if getattr(self, "_filter_require_confluence", None) and self._filter_require_confluence.isChecked():
            ind = res.indicators or {}
            if res.setup.direction == "LONG":
                if not ind.get("confluence_long_passed", False):
                    return False
            else:
                if not ind.get("confluence_short_passed", False):
                    return False
        return True

    def _update_setup_panel(self) -> None:
        sym = self._combo_symbol.currentText() or getattr(self, "_last_analysis_symbol", "") or "--"
        intv = getattr(self, "_last_analysis_interval", "")
        lbl = f"Seçili parite: {sym}" + (f" ({intv})" if intv else "")
        self._selected_symbol_label.setText(lbl)
        res = self._last_result
        if not res:
            if hasattr(self, "_filter_reject_label"):
                self._filter_reject_label.setText("")
            return

        setup = res.setup
        filtered = (
            getattr(self, "_filter_high_quality", None)
            and self._filter_high_quality.isChecked()
            and setup
            and not self._passes_quality_filter(res)
        )
        if hasattr(self, "_filter_reject_label"):
            if filtered and setup:
                self._filter_reject_label.setText(
                    f"Bu sinyal filtrelendi: guven {setup.confidence}/10"
                    + (", confluence gecmedi" if (self._filter_require_confluence and self._filter_require_confluence.isChecked()) else "")
                )
            else:
                self._filter_reject_label.setText("")

        if setup is None:
            self._dir_label.setText("BEKLE")
            self._dir_label.setStyleSheet("color: #ff9800;")
            self._confidence_label.setText(res.summary)
            self._sl_rule_label.setText("")

            prec = self._price_precision(res.indicators.get("close", 0))
            rows = []
            if res.wait_for_long is not None:
                rows.append(("Long için bekle", f"${res.wait_for_long:,.{prec}f}", GREEN))
            if res.wait_for_short is not None:
                rows.append(("Short için bekle", f"${res.wait_for_short:,.{prec}f}", RED))
            acct = self._get_account_size()
            rows.append((f"Bakiye (${acct:.0f})", "Max risk: %2/islem", "#ff9800"))
            rows.append(("Kaldirac", "Bekle - setup yok", "#9e9e9e"))

            self._setup_table.setRowCount(len(rows))
            for i, (label, val, clr) in enumerate(rows):
                self._setup_table.setItem(i, 0, QTableWidgetItem(label))
                vi = QTableWidgetItem(val)
                vi.setForeground(QColor(clr))
                self._setup_table.setItem(i, 1, vi)
            self._reason_table.setRowCount(0)
            return

        color = _DIRECTION_COLORS.get(setup.direction, "#ffffff")
        if filtered:
            self._dir_label.setText(f"{setup.direction} (filtrelendi)")
            self._dir_label.setStyleSheet(f"color: {ORANGE};")
        else:
            self._dir_label.setText(setup.direction)
            self._dir_label.setStyleSheet(f"color: {color};")
        acct = self._get_account_size()
        lev = _leverage_from_confidence(setup.confidence)
        risk_pct = _risk_pct_from_confidence(setup.confidence)
        risk_usd = acct * (risk_pct / 100)
        pos_usd = _position_usd_from_risk(risk_pct, setup.risk_pct, acct, setup.confidence)
        self._confidence_label.setText(
            f"Guven: {setup.confidence}/10  |  Risk: %{risk_pct:.1f} (${risk_usd:.1f})  |  Kaldirac: max {lev}x"
        )

        # SL kurali: TP1 vurulunca breakeven, TP2'ye yaklasinca SL TP1'e
        self._sl_rule_label.setText(
            "SL Kurali: TP1 vurulunca SL'i breakeven (entry) cek. TP2'ye yaklasinca SL'i TP1'e tasi."
        )

        if res.indicators.get("spread_warning"):
            self._confidence_label.setText(
                self._confidence_label.text() + "  |  " + res.indicators["spread_warning"]
            )
        if res.indicators.get("spread_bps") and res.indicators["spread_bps"] > 25:
            self._confidence_label.setText(
                self._confidence_label.text() + f"  |  Yuksek spread: {res.indicators['spread_bps']:.0f} bps"
            )
        # Ters sinyal uyarisi: Pozisyonum LONG iken SHORT cikti veya tersi
        my_pos = self._combo_my_position.currentText()
        if my_pos == "LONG" and setup.direction == "SHORT":
            self._confidence_label.setText(
                self._confidence_label.text() + "  |  UYARI: Onceki LONG, simdi SHORT - tasiyabilirsiniz"
            )
        elif my_pos == "SHORT" and setup.direction == "LONG":
            self._confidence_label.setText(
                self._confidence_label.text() + "  |  UYARI: Onceki SHORT, simdi LONG - tasiyabilirsiniz"
            )

        acct = self._get_account_size()
        prec = self._price_precision(setup.entry)
        lev = _leverage_from_confidence(setup.confidence)
        risk_pct = _risk_pct_from_confidence(setup.confidence)
        pos_usd = _position_usd_from_risk(risk_pct, setup.risk_pct, acct, setup.confidence)
        risk_usd = acct * (risk_pct / 100)

        limit_entry = getattr(setup, "limit_entry", 0.0) or 0.0
        ez_low = getattr(setup, "entry_zone_low", 0.0) or 0.0
        ez_high = getattr(setup, "entry_zone_high", 0.0) or 0.0
        tp_pri = getattr(setup, "tp_priority", "") or ""
        rows = [
            ("Entry (Giriş)", f"{setup.entry:.{prec}f}", "#eaecef"),
            ("Limit (pullback)", f"{limit_entry:.{prec}f}" if limit_entry else "-", "#9e9e9e"),
            ("Giriş Bölgesi", f"{ez_low:.{prec}f} - {ez_high:.{prec}f}" if ez_low and ez_high and ez_low != ez_high else "-", "#9e9e9e"),
            ("TP Öncelik", tp_pri if tp_pri else "-", "#9e9e9e"),
            ("Stop-Loss", f"{setup.stop_loss:.{prec}f}", RED),
            ("TP1 (R:R {:.1f})".format(setup.rr1), f"{setup.tp1:.{prec}f}", GREEN),
            ("TP2 (R:R {:.1f})".format(setup.rr2), f"{setup.tp2:.{prec}f}", GREEN),
            ("TP3 (R:R {:.1f})".format(setup.rr3), f"{setup.tp3:.{prec}f}", GREEN),
            ("Risk % (güven)", f"%{risk_pct:.1f} (${risk_usd:.1f})", ORANGE),
            ("Kaldıraç (öneri)", f"Max {lev}x", BLUE),
            (f"Pozisyon (${acct:.0f} hesap)", f"~${pos_usd:.0f}", GREEN),
        ]

        self._setup_table.setRowCount(len(rows))
        for i, (label, val, clr) in enumerate(rows):
            label_item = QTableWidgetItem(label)
            label_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            label_item.setToolTip(label)
            self._setup_table.setItem(i, 0, label_item)

            val_item = QTableWidgetItem(val)
            val_item.setFont(QFont("Consolas", 10))
            val_item.setToolTip(val)
            val_item.setForeground(QColor(clr))
            self._setup_table.setItem(i, 1, val_item)

        self._reason_table.setRowCount(len(setup.reasons))
        for i, reason in enumerate(setup.reasons):
            item = QTableWidgetItem(reason)
            item.setToolTip(reason)
            self._reason_table.setItem(i, 0, item)

        # Korelasyon uyarisi
        symbol = self._combo_symbol.currentText()
        if symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
            self._sl_rule_label.setText(
                self._sl_rule_label.text() + "  Korelasyon: BTC/ETH/SOL ayni anda hepsinde pozisyon acmayin."
            )
        # Sembol performans uyarisi
        if symbol in self._weak_symbols:
            self._confidence_label.setText(
                self._confidence_label.text() + "  |  UYARI: Bu parite backtest'te zayif"
            )

    def _update_indicator_panel(self) -> None:
        res = self._last_result
        if not res or not res.indicators:
            return

        ind = res.indicators
        prec = self._price_precision(ind.get("close", 0))

        fr_str = f"%{ind['funding_rate']*100:.4f}" if ind.get("funding_rate") is not None else "-"
        vol_str = f"%{ind['volatility_pct']:.2f}" if ind.get("volatility_pct") is not None else "-"
        liq_str = "UYARI: Dusuk likidite" if ind.get("liquidity_warning") else "Normal"
        rows = [
            ("Fiyat", f"{ind['close']:.{prec}f}"),
            ("RSI (14)", f"{ind['rsi']:.1f}"),
            ("MACD", f"{ind['macd_line']:.{prec}f}"),
            ("MACD Hist", f"{ind['macd_hist']:.{prec}f}"),
            ("MACD Sinyal", f"{ind['macd_signal']:.{prec}f}"),
            ("BB Ust", f"{ind['bb_upper']:.{prec}f}"),
            ("BB Alt", f"{ind['bb_lower']:.{prec}f}"),
            ("EMA 50", f"{ind['ema50']:.{prec}f}"),
            ("ATR (14)", f"{ind['atr']:.{prec}f}"),
            ("ADX", f"{ind.get('adx', 0):.1f}"),
            ("Regime", f"{ind.get('regime', '-')} ({ind.get('regime_strength', '-')})"),
            ("ATR % Dilim", f"{ind.get('atr_percentile', 50):.0f}" if ind.get("atr_percentile") is not None else "-"),
            ("OBV Trend", {"bullish": "Yukselis", "bearish": "Dusus", "neutral": "Notr"}.get(ind.get("obv_trend", ""), "-")),
            ("OBV Divergence", ind.get("obv_divergence", "-") or "-"),
            ("Likidite Grab", ind.get("liquidity_grab", "-") or "-"),
            ("OB Yığılma L/S", f"{ind.get('ob_stack_long', 0)}/{ind.get('ob_stack_short', 0)}"),
            ("MTF Yapı Uyum", "Evet" if ind.get("mtf_structure_aligned") else "Hayır"),
            ("BB %B", f"{ind.get('bb_pct_b', 0.5):.2f}"),
            ("Stoch RSI", f"{ind.get('stoch_rsi_k', 50):.1f}"),
            ("Volatilite", vol_str),
            ("Funding Rate", fr_str),
            ("Likidite", liq_str),
            ("Trend", {"up": "Yukselis", "down": "Dusus", "sideways": "Yatay"}.get(ind["trend"], "-")),
            ("Market Structure", ind.get("market_structure", "-") or "-"),
            ("BOS", ind.get("market_structure_bos", "-") or "-"),
            ("CHoCH", ind.get("market_structure_choch", "-") or "-"),
            ("Likidite Havuzu", "Yakin" if ind.get("liquidity_pool_near") else "Uzak"),
            ("Hafta Sonu", "Evet" if ind.get("session_weekend") else "Hayir"),
            ("NY/London", "Acilis" if ind.get("session_ny_london") else "-"),
            ("Seans Win Rate", MainWindow._fmt_session_wr(ind.get("session_win_rate"))),
            ("VWAP", f"{ind['vwap']:.{prec}f}" if ind.get("vwap") is not None else "-"),
            ("Fiyat vs VWAP", ind.get("price_vs_vwap", "-") or "-"),
            ("Onceki Gun H", f"{ind['prev_day_high']:.{prec}f}" if ind.get("prev_day_high") is not None else "-"),
            ("Onceki Gun L", f"{ind['prev_day_low']:.{prec}f}" if ind.get("prev_day_low") is not None else "-"),
            ("Open Interest", f"{ind['open_interest']:,.0f}" if ind.get("open_interest") is not None else "-"),
            ("Funding Trend", ind.get("funding_trend", "-") or "-"),
            ("Destek", f"{ind['support']:.{prec}f}" if ind.get("support") else "-"),
            ("Direnc", f"{ind['resistance']:.{prec}f}" if ind.get("resistance") else "-"),
            ("Hacim", f"{ind['volume']:,.0f}"),
            ("Hacim Ort", f"{ind['vol_avg']:,.0f}"),
        ]
        if ind.get("order_book_imbalance") is not None:
            imb = ind["order_book_imbalance"]
            imb_str = f"{imb:+.2f} (bid>ask=LONG)" if imb > 0 else f"{imb:+.2f} (ask>bid=SHORT)"
            rows.append(("Order Book", imb_str))
        if ind.get("confluence_long") is not None:
            cl, cs = ind.get("confluence_long", 0), ind.get("confluence_short", 0)
            pl, ps = ind.get("confluence_long_passed", False), ind.get("confluence_short_passed", False)
            rows.append(("Confluence LONG", f"{cl}/10 {'OK' if pl else 'Yetersiz'}"))
            rows.append(("Confluence SHORT", f"{cs}/10 {'OK' if ps else 'Yetersiz'}"))
        if ind.get("spread_bps") is not None:
            rows.append(("Spread", f"{ind['spread_bps']:.1f} bps"))
        if ind.get("session_warning"):
            rows.append(("Seans Uyarisi", ind["session_warning"]))
        if ind.get("economic_warning"):
            rows.append(("Ekonomik Takvim", ind["economic_warning"]))
        if ind.get("volatility_warning"):
            rows.append(("Volatilite Uyarisi", "Yuksek - dikkatli ol"))
        if ind.get("volume_confirmation") is not None:
            rows.append(("Hacim Onayi", "OK" if ind["volume_confirmation"] else f"Zayif ({ind.get('volume_ratio', 0):.1f}x)"))
        if ind.get("macd_divergence"):
            rows.append(("MACD Divergence", ind["macd_divergence"]))
        if ind.get("structure_break") and ind["structure_break"].get("choch"):
            rows.append(("CHoCH", ind["structure_break"]["choch"]))
        if ind.get("fib_levels"):
            fib = ind["fib_levels"]
            f618 = fib.get("fib_0.618")
            if f618 is not None:
                rows.append(("Fib 0.618", f"{f618:.2f}"))
        if ind.get("volume_profile"):
            vp = ind["volume_profile"]
            rows.append(("POC (Volume)", f"{vp.get('poc', 0):.2f}"))
        if ind.get("liquidations_24h"):
            liq = ind["liquidations_24h"]
            if isinstance(liq, dict) and liq.get("count", 0) > 0:
                rows.append(("Liquidasyon (son)", f"Long ${liq.get('long_liq_usd',0):.0f} | Short ${liq.get('short_liq_usd',0):.0f} ({liq.get('count',0)} adet)"))
            elif isinstance(liq, dict) and liq.get("note"):
                rows.append(("Liquidasyon", liq.get("note", "-")))
        if ind.get("fear_greed_index") is not None:
            rows.append(("Fear & Greed", str(ind["fear_greed_index"])))
        if ind.get("exchange_flow"):
            rows.append(("On-chain/Whale", ind["exchange_flow"]))
        if ind.get("ensemble_long_b") is not None and ind.get("ensemble_short_b") is not None:
            rows.append(("Ensemble (basit)", f"L {ind['ensemble_long_b']} / S {ind['ensemble_short_b']}"))

        self._indicator_table.setRowCount(len(rows))
        for i, (name, val) in enumerate(rows):
            self._indicator_table.setItem(i, 0, QTableWidgetItem(name))
            self._indicator_table.setItem(i, 1, QTableWidgetItem(val))

        setup = res.setup
        if setup and ind.get("confluence_criteria"):
            criteria = ind["confluence_criteria"]
            direction = setup.direction
            total = ind.get("confluence_long" if direction == "LONG" else "confluence_short", 0)
            passed = ind.get("confluence_long_passed" if direction == "LONG" else "confluence_short_passed", False)
            self._confluence_score_label.setText(f"Confluence {direction}: {total}/10 (min 6) {'OK' if passed else 'Yetersiz'}")
            self._confluence_table.setRowCount(len(criteria))
            for i, (name, ok, detail) in enumerate(criteria):
                self._confluence_table.setItem(i, 0, QTableWidgetItem(name))
                ok_item = QTableWidgetItem("Evet" if ok else "Hayir")
                ok_item.setForeground(QColor(GREEN if ok else "#9e9e9e"))
                self._confluence_table.setItem(i, 1, ok_item)
                self._confluence_table.setItem(i, 2, QTableWidgetItem(str(detail)[:40]))
        else:
            self._confluence_score_label.setText("Confluence: --")
            self._confluence_table.setRowCount(0)

    @staticmethod
    def _fmt_session_wr(sess: dict | None) -> str:
        """Seans win rate formatla."""
        if not sess or not isinstance(sess, dict):
            return "-"
        cur = sess.get("current", "")
        cur_data = sess.get(cur, {}) if cur else {}
        wr = cur_data.get("win_rate")
        n = cur_data.get("trades", 0)
        if wr is not None and n >= 0:
            return f"{cur} %{wr} ({n}t)"
        return "-"

    @staticmethod
    def _price_precision(price) -> int:
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

    def _on_notifications_toggled(self, state: int) -> None:
        enabled = state == Qt.Checked
        settings = QSettings("BinanceTA", "TeknikAnaliz")
        settings.setValue("notifications_enabled", enabled)

    def _maybe_notify_setup(self, symbol: str, interval: str) -> None:
        """LONG/SHORT setup geldiginde sesli + Windows bildirimi."""
        res = self._last_result
        if not res or not res.setup:
            return
        setup = res.setup
        key = f"{symbol}_{interval}_{setup.direction}_{setup.entry:.4f}"
        if key == self._last_notified_key:
            return
        self._last_notified_key = key

        from notifications import notify_setup

        notify_setup(
            symbol=symbol,
            interval=interval,
            direction=setup.direction,
            entry=setup.entry,
            confidence=setup.confidence,
            windows=self._chk_notifications.isChecked(),
            stop_loss=setup.stop_loss,
            tp1=setup.tp1,
            tp2=setup.tp2,
            tp3=setup.tp3,
            limit_entry=getattr(setup, "limit_entry", 0) or None,
        )
        if self._chk_notifications.isChecked():
            try:
                import winsound
                winsound.Beep(1000, 150)
            except Exception:
                pass

    # ==================================================================
    # Signal history DB
    # ==================================================================

    def _save_signal_to_db(self) -> None:
        res = self._last_result
        if not res or not res.setup:
            return

        setup = res.setup
        symbol = self._combo_symbol.currentText()
        interval = self._combo_interval.currentText()

        mode = "scalp" if self._combo_mode.currentIndex() == 2 else ("long" if self._combo_mode.currentIndex() == 1 else "short")
        self._last_signal_id = save_signal(
            symbol, interval, setup.direction, setup.confidence, setup.reasons, setup.entry,
            mode=mode, setup_type=getattr(setup, "setup_type", "") or "",
        )

    def _on_load_history(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or None
            if symbol == "":
                symbol = None
            dir_idx = self._history_filter_direction.currentIndex()
            direction = None if dir_idx == 0 else ("LONG" if dir_idx == 1 else "SHORT")
            min_str = self._history_filter_min_strength.value()
            min_strength = None if min_str == 0 else min_str
            records = get_history_filtered(symbol=symbol, direction=direction, min_strength=min_strength, limit=200)
        except Exception as e:
            self._stats_label.setText(f"Hata: {e}")
            return

        try:
            self._history_table.setRowCount(len(records))
            for i, r in enumerate(records):
                self._history_table.setItem(i, 0, QTableWidgetItem(r.timestamp))
                self._history_table.setItem(i, 1, QTableWidgetItem(r.symbol))
                self._history_table.setItem(i, 2, QTableWidgetItem(r.interval))

                dir_item = QTableWidgetItem(r.direction)
                color = _DIRECTION_COLORS.get(r.direction, "#ffffff")
                dir_item.setForeground(QColor(color))
                self._history_table.setItem(i, 3, dir_item)

                self._history_table.setItem(i, 4, QTableWidgetItem(str(r.strength)))
                prec = self._price_precision(r.price_at_signal)
                self._history_table.setItem(i, 5, QTableWidgetItem(f"{r.price_at_signal:.{prec}f}"))
                self._history_table.setItem(i, 6, QTableWidgetItem(
                    f"{r.price_after:.{prec}f}" if r.price_after else "-"
                ))
                result_item = QTableWidgetItem(
                    f"{r.result_pct:+.2f}%" if r.result_pct is not None else "-"
                )
                if r.result_pct is not None:
                    result_item.setForeground(
                        QColor("#4caf50") if r.result_pct > 0 else QColor("#f44336")
                    )
                self._history_table.setItem(i, 7, result_item)

            stats = get_stats(symbol)
            self._stats_label.setText(
                f"Toplam: {stats['total']}  |  "
                f"LONG: {stats['buy']}  SHORT: {stats['sell']}  |  "
                f"Degerlendirilen: {stats['evaluated']}  |  "
                f"Basari: %{stats['win_rate']}  |  "
                f"Ort. Sonuc: %{stats['avg_result_pct']}"
            )
        except Exception as e:
            self._stats_label.setText(f"Hata: {e}")

    def _on_tab_changed(self, idx: int) -> None:
        try:
            tab_name = self._tabs.tabText(idx)
            if tab_name == "Scalp":
                self._scalp_price_timer.start(5_000)
                self._scalp_analysis_timer.start(30_000)
                self._scalp_fetch_price()
                if not self._scalp_analysis_running:
                    self._on_scalp_analyze()
            else:
                self._scalp_price_timer.stop()
                self._scalp_analysis_timer.stop()
            if tab_name == "Gercek Islemler":
                sym = self._combo_symbol.currentText()
                if sym and self._tr_symbol.findText(sym) == -1:
                    self._tr_symbol.addItem(sym)
                self._tr_symbol.setCurrentText(sym or "BTCUSDT")
                self._tr_interval.setCurrentText(self._combo_interval.currentText())
                self._load_trade_results_tab()
            elif tab_name == "Grafik Tahmini":
                sym = self._combo_symbol.currentText()
                if sym and self._pred_symbol.findText(sym) == -1:
                    self._pred_symbol.addItem(sym)
                self._pred_symbol.setCurrentText(sym or "BTCUSDT")
            elif tab_name == "Paper Trading":
                self._refresh_paper_tables()
            elif tab_name == "Pozisyon Hesaplayici":
                self._pc_account.setValue(int(self._get_account_size()))
                if self._live_price > 0:
                    self._pc_entry.setValue(self._live_price)
        except Exception as e:
            self._status_label.setText(f"Hata: {e}")

    def _on_delete_trade_result(self) -> None:
        row = self._trade_results_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Uyari", "Silmek icin bir islem secin.")
            return
        id_item = self._trade_results_table.item(row, 0)
        if not id_item:
            return
        trade_id = int(id_item.text())
        if QMessageBox.question(
            self, "Onay", f"Islem #{trade_id} silinsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        if delete_trade_result(trade_id):
            self._load_trade_results_tab()
        else:
            QMessageBox.warning(self, "Hata", "Islem silinemedi.")

    def _on_add_trade_result(self) -> None:
        symbol = self._tr_symbol.currentText().strip().upper() or "BTCUSDT"
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        interval = self._tr_interval.currentText()
        direction = self._tr_direction.currentText()
        entry = self._tr_entry.value()
        exit_p = self._tr_exit.value()
        conf = self._tr_confidence.value()
        try:
            add_trade_result(symbol, interval, direction, entry, exit_p, conf)
        except Exception as exc:
            QMessageBox.warning(self, "Hata", str(exc))
            return
        self._load_trade_results_tab()

    def _load_trade_results_tab(self) -> None:
        try:
            records = get_trade_results(100)
            self._trade_results_table.setRowCount(len(records))
            for i, r in enumerate(records):
                self._trade_results_table.setItem(i, 0, QTableWidgetItem(str(r.id)))
                self._trade_results_table.setItem(i, 1, QTableWidgetItem(r.timestamp))
                self._trade_results_table.setItem(i, 2, QTableWidgetItem(r.symbol))
                self._trade_results_table.setItem(i, 3, QTableWidgetItem(r.interval))
                dir_item = QTableWidgetItem(r.direction)
                dir_item.setForeground(QColor(_DIRECTION_COLORS.get(r.direction, "#fff")))
                self._trade_results_table.setItem(i, 4, dir_item)
                self._trade_results_table.setItem(i, 5, QTableWidgetItem(f"{r.entry_price:.4f}"))
                self._trade_results_table.setItem(i, 6, QTableWidgetItem(f"{r.exit_price:.4f}"))
                res_item = QTableWidgetItem(f"{r.result_pct:+.2f}%")
                res_item.setForeground(QColor("#4caf50") if r.result_pct > 0 else QColor("#f44336"))
                self._trade_results_table.setItem(i, 7, res_item)
            calib = get_calibration_stats(None, min_evaluated=5)
            self._trade_results_stats.setText(
                f"Toplam: {len(records)} işlem  |  Kalibrasyon: min güven {calib['calibrated_min']}  |  "
                f"Bantlar: {' '.join(f'{k} %{v}' for k, v in calib.get('by_band', {}).items())}"
            )
        except Exception as e:
            self._trade_results_stats.setText(f"Hata: {e}")

    def _on_update_results(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or "BTCUSDT"
            records = get_history(symbol, limit=200)
            from data_fetcher import fetch_ticker_price
            current = fetch_ticker_price(symbol)
        except Exception as e:
            self._stats_label.setText(f"Hata: {e}")
            return
        try:
            for r in records:
                if r.result_pct is None and r.direction in ("LONG", "SHORT", "AL", "SAT"):
                    update_result(r.id, current)
            self._on_load_history()
        except Exception as e:
            self._stats_label.setText(f"Hata: {e}")

    # ==================================================================
    # Multi-timeframe
    # ==================================================================

    def _on_run_mtf(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or "BTCUSDT"
            self._mtf_consensus.setText("Analiz ediliyor...")
            result: MTFResult = run_mtf_analysis(symbol)
        except Exception as exc:
            self._mtf_consensus.setText(f"Hata: {exc}")
            return

        try:
            color = _DIRECTION_COLORS.get(result.consensus, "#ffffff")
            self._mtf_consensus.setText(f"Sonuc: {result.consensus}")
            self._mtf_consensus.setStyleSheet(f"color: {color};")
            prec = self._price_precision(result.current_price)
            self._mtf_summary.setText(f"Fiyat: ${result.current_price:,.{prec}f}  |  {result.summary}")

            self._mtf_table.setRowCount(len(result.analyses))
            for i, a in enumerate(result.analyses):
                self._mtf_table.setItem(i, 0, QTableWidgetItem(a.interval))
                self._mtf_table.setItem(i, 1, QTableWidgetItem(f"${a.close:,.{prec}f}"))
                trend_item = QTableWidgetItem(
                    {"up": "Yukselis", "down": "Dusus", "sideways": "Yatay"}[a.trend]
                )
                trend_color = {"up": "#4caf50", "down": "#f44336", "sideways": "#ff9800"}[a.trend]
                trend_item.setForeground(QColor(trend_color))
                self._mtf_table.setItem(i, 2, trend_item)

                rsi_item = QTableWidgetItem(f"{a.rsi:.1f}")
                if a.rsi < 30:
                    rsi_item.setForeground(QColor("#4caf50"))
                elif a.rsi > 70:
                    rsi_item.setForeground(QColor("#f44336"))
                self._mtf_table.setItem(i, 3, rsi_item)
                self._mtf_table.setItem(i, 4, QTableWidgetItem(f"{a.macd_hist:.4f}"))
                ema_item = QTableWidgetItem("Evet" if a.above_ema50 else "Hayir")
                ema_item.setForeground(QColor("#4caf50") if a.above_ema50 else QColor("#f44336"))
                self._mtf_table.setItem(i, 5, ema_item)
                bb_map = {"above": "Ust bant ustu", "inside": "Bantlar icinde", "below": "Alt bant alti"}
                self._mtf_table.setItem(i, 6, QTableWidgetItem(bb_map[a.bb_position]))
        except Exception as exc:
            self._mtf_consensus.setText(f"Hata: {exc}")

    # ==================================================================
    # Backtest
    # ==================================================================

    def _on_run_backtest(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or "BTCUSDT"
            interval = self._combo_interval.currentText()
            limit = int(self._combo_limit.currentText())
            self._bt_summary.setText("Backtest calisiyor...")
            df = safe_fetch_klines(symbol, interval, limit)
        except Exception as exc:
            self._bt_summary.setText(f"Veri hatasi: {exc}")
            return

        try:
            scalp_mode = self._combo_mode.currentIndex() == 2
            result: BacktestResult = run_backtest(
                df, symbol=symbol, interval=interval,
                stop_loss_atr_mult=self._spin_sl.value(),
                take_profit_atr_mult=self._spin_tp.value(),
                min_signal_strength=self._spin_min_str.value(),
                commission_pct=self._spin_comm.value(),
                scalp=scalp_mode,
            )

            calmar_str = f"{result.calmar_ratio:.2f}" if result.calmar_ratio != float("inf") else "inf"
            summary_text = (
                f"Toplam Islem: {result.total_trades}  |  "
                f"Kazanc: {result.winning_trades}  Kayip: {result.losing_trades}  |  "
                f"Basari: %{result.win_rate}  |  "
                f"Toplam PnL: %{result.total_pnl_pct:.2f}  |  "
                f"Ort: %{result.avg_pnl_pct:.2f}  |  "
                f"Max Kar: %{result.max_win_pct:.2f}  Max Zarar: %{result.max_loss_pct:.2f}  |  "
                f"Profit Factor: {result.profit_factor}  |  "
                f"Max Drawdown: %{result.max_drawdown_pct:.2f}  |  Calmar: {calmar_str}"
            )
            if result.win_rate < 40 or (result.profit_factor < 1.0 and result.total_trades > 0):
                summary_text += "\n\nUYARI: Bu parite/dilim backtest'te zayif - dikkatli kullanin."
            self._bt_summary.setText(summary_text)

            self._bt_table.setRowCount(len(result.trades))
            for i, t in enumerate(result.trades):
                self._bt_table.setItem(i, 0, QTableWidgetItem(t.entry_time))
                dir_item = QTableWidgetItem(t.direction)
                dir_item.setForeground(QColor(_DIRECTION_COLORS.get(t.direction, "#ffffff")))
                self._bt_table.setItem(i, 1, dir_item)
                self._bt_table.setItem(i, 2, QTableWidgetItem(f"{t.entry_price:.2f}"))
                self._bt_table.setItem(i, 3, QTableWidgetItem(t.exit_time))
                self._bt_table.setItem(i, 4, QTableWidgetItem(f"{t.exit_price:.2f}"))
                pnl_item = QTableWidgetItem(f"{t.pnl_pct:+.2f}%")
                pnl_item.setForeground(QColor("#4caf50") if t.pnl_pct > 0 else QColor("#f44336"))
                self._bt_table.setItem(i, 5, pnl_item)
                self._bt_table.setItem(i, 6, QTableWidgetItem(t.exit_reason))
        except Exception as exc:
            self._bt_summary.setText(f"Hata: {exc}")

    def _on_optimize_backtest(self) -> None:
        try:
            symbol = self._combo_symbol.currentText() or "BTCUSDT"
            interval = self._combo_interval.currentText()
            limit = int(self._combo_limit.currentText())
            self._bt_summary.setText("Optimize ediliyor...")
            df = safe_fetch_klines(symbol, interval, limit)
        except Exception as exc:
            self._bt_summary.setText(f"Veri hatasi: {exc}")
            return
        try:
            scalp_mode = self._combo_mode.currentIndex() == 2
            params, result = optimize_backtest(df, symbol, interval, self._spin_comm.value(), scalp=scalp_mode)
            self._spin_sl.setValue(params["sl"])
            self._spin_tp.setValue(params["tp"])
            self._spin_min_str.setValue(params["min_str"])
            calmar_str = f"{result.calmar_ratio:.2f}" if result.calmar_ratio != float("inf") else "inf"
            self._bt_summary.setText(
                f"Optimize: SL={params['sl']} TP={params['tp']} MinStr={params['min_str']} | "
                f"Win:%{result.win_rate} PF:{result.profit_factor} PnL:%{result.total_pnl_pct:.2f} "
                f"MaxDD:%{result.max_drawdown_pct:.2f} Calmar:{calmar_str}"
            )
            self._bt_table.setRowCount(len(result.trades))
            for i, t in enumerate(result.trades):
                self._bt_table.setItem(i, 0, QTableWidgetItem(t.entry_time))
                dir_item = QTableWidgetItem(t.direction)
                dir_item.setForeground(QColor(_DIRECTION_COLORS.get(t.direction, "#ffffff")))
                self._bt_table.setItem(i, 1, dir_item)
                self._bt_table.setItem(i, 2, QTableWidgetItem(f"{t.entry_price:.2f}"))
                self._bt_table.setItem(i, 3, QTableWidgetItem(t.exit_time))
                self._bt_table.setItem(i, 4, QTableWidgetItem(f"{t.exit_price:.2f}"))
                pnl_item = QTableWidgetItem(f"{t.pnl_pct:+.2f}%")
                pnl_item.setForeground(QColor("#4caf50") if t.pnl_pct > 0 else QColor("#f44336"))
                self._bt_table.setItem(i, 5, pnl_item)
                self._bt_table.setItem(i, 6, QTableWidgetItem(t.exit_reason))
        except Exception as exc:
            self._bt_summary.setText(f"Hata: {exc}")

    def _on_symbol_performance(self) -> None:
        self._bt_summary.setText("Sembol performansi hesaplaniyor...")
        symbols = [self._combo_symbol.itemText(i) for i in range(self._combo_symbol.count())]
        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
        interval = self._combo_interval.currentText()
        scalp_mode = self._combo_mode.currentIndex() == 2
        try:
            results = get_symbol_performance(symbols, interval, 300, scalp=scalp_mode)
        except Exception as exc:
            self._bt_summary.setText(f"Hata: {exc}")
            return
        self._weak_symbols = {sym for sym, r in results[-5:] if r.total_trades > 0 and (r.win_rate < 40 or r.profit_factor < 1.0)}
        self._bt_summary.setText(
            "Sembol performansi (en iyiden): " + ", ".join(f"{s}(%{r.win_rate:.0f})" for s, r in results[:8])
        )
        if self._weak_symbols:
            self._bt_summary.setText(self._bt_summary.text() + " | Zayif: " + ", ".join(self._weak_symbols))

    # ==================================================================
    # Disclaimer
    # ==================================================================

    def _show_disclaimer(self) -> None:
        dlg = DisclaimerDialog(self)
        dlg.exec_()

    # ==================================================================
    # Cleanup
    # ==================================================================

    def closeEvent(self, event) -> None:
        self._ws.disconnect()
        super().closeEvent(event)
