"""Grafik tahmini - mum grafik, tahmin oklari, bilgi tablosu. PANDAS YOK - sadece numpy/list."""
import matplotlib
matplotlib.use("Agg")

import numpy as np
from dataclasses import dataclass
from typing import Literal, Optional, List, Tuple

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHeaderView,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class PredictionResult:
    phase1: Literal["YUKSELIR", "ALCALIR", "YATAY"]
    phase2: Optional[Literal["YUKSELIR", "ALCALIR", "YATAY"]]
    summary: str
    confidence: float = 0.5  # 0-1: tahmin guveni (projeksiyon buyuklugu icin)


def _safe_float(x, default: float = 0.0) -> float:
    try:
        f = float(x)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def get_prediction(
    setup_direction: Optional[str],
    mtf_1h: Optional[str],
    mtf_4h: Optional[str],
    trend: str,
    rsi: Optional[float] = None,
    macd_hist: Optional[float] = None,
    close_vs_ema50: Optional[bool] = None,
    indicators: Optional[dict] = None,
) -> PredictionResult:
    """PDF + Price Action: Tum teknik analiz verileriyle ust seviye tahmin."""
    ind = indicators or {}

    # Ust seviye: long_score/short_score varsa dogrudan kullan (signal_engine ile ayni mantik)
    long_score = _safe_float(ind.get("long_score"), -1)
    short_score = _safe_float(ind.get("short_score"), -1)

    if long_score >= 0 and short_score >= 0:
        return _prediction_from_scores(long_score, short_score, setup_direction, mtf_1h, mtf_4h, trend, ind)

    # Fallback: eski mantik (MTF + trend + RSI + MACD + EMA)
    return _prediction_fallback(setup_direction, mtf_1h, mtf_4h, trend, rsi, macd_hist, close_vs_ema50)


def _prediction_from_scores(
    long_score: float,
    short_score: float,
    setup_direction: Optional[str],
    mtf_1h: Optional[str],
    mtf_4h: Optional[str],
    trend: str,
    ind: dict,
) -> PredictionResult:
    """PDF kurallari: Skor farki, Turtle, divergence, chart patterns, destek/direnc yakınlığı."""
    pred_up = long_score
    pred_down = short_score

    # Ek agirliklar (grafik okuma)
    turtle = ind.get("turtle")
    if turtle == "LONG":
        pred_up += 1.5
    elif turtle == "SHORT":
        pred_down += 1.5

    div = ind.get("divergence")
    if div == "bullish":
        pred_up += 1.5
    elif div == "bearish":
        pred_down += 1.5

    cp_bull = ind.get("chart_patterns_bullish", 0) or 0
    cp_bear = ind.get("chart_patterns_bearish", 0) or 0
    pred_up += cp_bull
    pred_down += cp_bear

    # Destek/direnc context (Galen Woods: pattern at level)
    near_sup = ind.get("near_support", False)
    near_res = ind.get("near_resistance", False)
    if near_sup:
        pred_up += 1
    if near_res:
        pred_down += 1

    # ADX: guclu trend = tahmin daha kesin
    adx = _safe_float(ind.get("adx"), 0)
    if adx > 25:
        diff = abs(pred_up - pred_down)
        if pred_up > pred_down:
            pred_up += min(diff * 0.2, 1)
        else:
            pred_down += min(diff * 0.2, 1)

    # MTF uyumu
    if mtf_1h == "LONG":
        pred_up += 0.5
    elif mtf_1h == "SHORT":
        pred_down += 0.5
    if mtf_4h == "LONG":
        pred_up += 0.5
    elif mtf_4h == "SHORT":
        pred_down += 0.5

    # Setup varsa ek agirlik
    if setup_direction == "LONG":
        pred_up += 1
    elif setup_direction == "SHORT":
        pred_down += 1

    # Karar: her zaman yon ver (YATAY yok - LONG veya SHORT ok)
    # ONCELIK: setup_direction analiz sonucu - Grafik Tahmini ile Trade Setup ayni yonu gostermeli
    if setup_direction == "LONG":
        p1 = "YUKSELIR"
    elif setup_direction == "SHORT":
        p1 = "ALCALIR"
    elif pred_up > pred_down:
        p1 = "YUKSELIR"
    elif pred_down > pred_up:
        p1 = "ALCALIR"
    else:
        p1 = "YUKSELIR" if ind.get("trend") == "up" else "ALCALIR" if ind.get("trend") == "down" else "YUKSELIR"
        confidence = 0.55

    diff = abs(pred_up - pred_down)
    confidence = min(0.95, 0.5 + diff * 0.08) if diff > 0 else 0.6

    # Iki fazli senaryo (4H vs 1H uyumsuzlugu)
    p2 = None
    if setup_direction and mtf_4h and mtf_4h != setup_direction:
        if setup_direction == "LONG" and mtf_4h == "SHORT":
            p2 = "ALCALIR"
            return PredictionResult(p1, p2, "Once yukselir (1H), sonra 4H trende gore alcalir", confidence)
        if setup_direction == "SHORT" and mtf_4h == "LONG":
            p2 = "YUKSELIR"
            return PredictionResult(p1, p2, "Once alcalir (1H), sonra 4H trende gore yukselir", confidence)

    # Ozet metin
    if p1 == "YUKSELIR":
        reasons = []
        if setup_direction == "LONG":
            reasons.append("LONG setup")
        if turtle == "LONG":
            reasons.append("Turtle breakout")
        if div == "bullish":
            reasons.append("RSI divergence")
        if near_sup:
            reasons.append("destekte")
        summary = "Grafik yukselise devam eder" + (f" ({', '.join(reasons)})" if reasons else "")
        return PredictionResult(p1, p2, summary, confidence)
    if p1 == "ALCALIR":
        reasons = []
        if setup_direction == "SHORT":
            reasons.append("SHORT setup")
        if turtle == "SHORT":
            reasons.append("Turtle breakout")
        if div == "bearish":
            reasons.append("RSI divergence")
        if near_res:
            reasons.append("direncte")
        summary = "Grafik dususe devam eder" + (f" ({', '.join(reasons)})" if reasons else "")
        return PredictionResult(p1, p2, summary, confidence)
    return PredictionResult(p1, p2, "Grafik yukselise devam eder" if p1 == "YUKSELIR" else "Grafik dususe devam eder", confidence)


def _prediction_fallback(
    setup_direction: Optional[str],
    mtf_1h: Optional[str],
    mtf_4h: Optional[str],
    trend: str,
    rsi: Optional[float],
    macd_hist: Optional[float],
    close_vs_ema50: Optional[bool],
) -> PredictionResult:
    """Indicators yoksa eski mantik."""
    if setup_direction == "LONG":
        p1 = "YUKSELIR"
    elif setup_direction == "SHORT":
        p1 = "ALCALIR"
    else:
        up_score, down_score = 0, 0
        if trend == "up":
            up_score += 2
        elif trend == "down":
            down_score += 2
        if mtf_1h == "LONG":
            up_score += 1
        elif mtf_1h == "SHORT":
            down_score += 1
        if mtf_4h == "LONG":
            up_score += 1
        elif mtf_4h == "SHORT":
            down_score += 1
        if rsi is not None:
            if rsi < 30:
                up_score += 2
            elif rsi > 70:
                down_score += 2
            elif rsi > 52:
                up_score += 1
            elif rsi < 48:
                down_score += 1
        if macd_hist is not None:
            if macd_hist > 0:
                up_score += 1
            elif macd_hist < 0:
                down_score += 1
        if close_vs_ema50 is not None:
            if close_vs_ema50:
                up_score += 1
            else:
                down_score += 1
        p1 = "YUKSELIR" if up_score >= down_score else "ALCALIR"

    p2 = None
    conf = 0.7 if setup_direction else 0.5
    if setup_direction and mtf_4h and mtf_4h != setup_direction:
        if setup_direction == "LONG" and mtf_4h == "SHORT":
            return PredictionResult(p1, "ALCALIR", "Once yukselir, sonra alcalir (4h trend)", conf)
        if setup_direction == "SHORT" and mtf_4h == "LONG":
            return PredictionResult(p1, "YUKSELIR", "Once alcalir, sonra yukselir (4h trend)", conf)
    if p1 == "YUKSELIR":
        return PredictionResult(p1, p2, "Grafik yukselise devam eder", conf)
    return PredictionResult(p1, p2, "Grafik dususe devam eder", conf)


def _to_float(x, default: float = 0.0) -> float:
    try:
        f = float(x)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _project_candles(
    last_close: float,
    y_range: float,
    atr_val: float,
    pred: PredictionResult,
    n_candles: int,
    support: float,
    resistance: float,
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Teknik analiz ile tahmin edilen mumlari uret. LONG=yukselis, SHORT=dusus, YATAY=range.
    confidence: yuksek = daha buyuk hareket projeksiyonu."""
    o_list, h_list, l_list, c_list = [], [], [], []
    conf = getattr(pred, "confidence", 0.5)
    mult = 0.6 + conf * 0.8
    step = max(atr_val * 0.35 * mult, y_range * 0.01)
    price = last_close

    def add_candle(o: float, c: float, wick: float):
        o_list.append(o)
        h_list.append(max(o, c) + wick)
        l_list.append(min(o, c) - wick)
        c_list.append(c)

    n1 = n_candles // 2 if pred.phase2 else n_candles
    n2 = n_candles - n1 if pred.phase2 else 0

    # Faz 1
    if pred.phase1 == "YUKSELIR":
        max_up = (resistance - price) * 0.95 if resistance > price and resistance > 0 else step * n1
        move_per = max_up / max(n1, 1)
        for i in range(n1):
            o, c = price, price + move_per * (0.8 + 0.4 * (i / max(n1, 1)))
            c = min(c, price + max_up)
            add_candle(o, c, step * 0.3)
            price = c
    elif pred.phase1 == "ALCALIR":
        max_down = (price - support) * 0.95 if support < price and support > 0 else step * n1
        move_per = max_down / max(n1, 1)
        for i in range(n1):
            o, c = price, price - move_per * (0.8 + 0.4 * (i / max(n1, 1)))
            c = max(c, price - max_down)
            add_candle(o, c, step * 0.3)
            price = c
    else:
        for i in range(n1):
            wiggle = step * 0.4 * (1 if (i % 2 == 0) else -1)
            c = price + wiggle
            add_candle(price, c, step * 0.2)
            price = c

    # Faz 2 (once X sonra Y)
    if pred.phase2 and n2 > 0:
        if pred.phase2 == "YUKSELIR":
            max_up = (resistance - price) * 0.9 if resistance > price and resistance > 0 else step * n2
            move_per = max_up / max(n2, 1)
            for i in range(n2):
                o, c = price, price + move_per
                c = min(c, price + max_up)
                add_candle(o, c, step * 0.3)
                price = c
        elif pred.phase2 == "ALCALIR":
            max_down = (price - support) * 0.9 if support < price and support > 0 else step * n2
            move_per = max_down / max(n2, 1)
            for i in range(n2):
                o, c = price, price - move_per
                c = max(c, price - max_down)
                add_candle(o, c, step * 0.3)
                price = c

    return o_list, h_list, l_list, c_list


class ChartPredictionWidget(QWidget):
    """Mum grafik + tahmin oklari + tablo. Sadece list/numpy - PANDAS YOK."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas: FigureCanvas | None = None
        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Bilgi", "Deger"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setMaximumHeight(180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._splitter = QSplitter()
        self._splitter.setOrientation(Qt.Vertical)
        self._chart_container = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_container)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        self._splitter.addWidget(self._chart_container)
        self._splitter.addWidget(self._table)
        self._splitter.setSizes([400, 150])
        layout.addWidget(self._splitter)

    def plot_from_arrays(
        self,
        open_vals: List[float],
        high_vals: List[float],
        low_vals: List[float],
        close_vals: List[float],
        vol_vals: List[float],
        symbol: str,
        interval: str,
        prediction: PredictionResult,
        indicators: Optional[dict] = None,
    ) -> None:
        """Pandas olmadan - sadece list/numpy ile ciz."""
        try:
            self._draw_chart(
                open_vals, high_vals, low_vals, close_vals, vol_vals,
                symbol, interval, prediction, indicators
            )
        except Exception as e:
            self._show_error(str(e))
            self._fill_table_error(symbol, interval)

    def _draw_chart(
        self,
        open_vals: List[float],
        high_vals: List[float],
        low_vals: List[float],
        close_vals: List[float],
        vol_vals: List[float],
        symbol: str,
        interval: str,
        prediction: PredictionResult,
        indicators: Optional[dict],
    ) -> None:
        while self._chart_layout.count():
            w = self._chart_layout.takeAt(0).widget()
            if w:
                w.setParent(None)

        n = len(close_vals)
        if n < 30:
            self._show_error("Yetersiz veri (min 30 mum)")
            self._fill_table_error(symbol, interval)
            return

        o = np.array(open_vals, dtype=float)
        h = np.array(high_vals, dtype=float)
        l = np.array(low_vals, dtype=float)
        c = np.array(close_vals, dtype=float)
        v = np.array(vol_vals, dtype=float)

        last_close = _to_float(c[-1])
        y_min = float(np.nanmin(l))
        y_max = float(np.nanmax(h))
        y_range = max(y_max - y_min, 1e-10)

        # Binance tarzi koyu tema
        BG = "#131722"
        GRID = "#2b2f3a"
        TEXT = "#eaecef"
        GREEN = "#0ecb81"
        RED = "#f6465d"

        fig = Figure(figsize=(12, 7), dpi=100, facecolor=BG)
        ax_main = fig.add_subplot(211)
        ax_vol = fig.add_subplot(212, sharex=ax_main)

        for ax in (ax_main, ax_vol):
            ax.set_facecolor(BG)
            ax.tick_params(colors=TEXT, labelsize=9)
            ax.xaxis.label.set_color(TEXT)
            ax.yaxis.label.set_color(TEXT)
            ax.spines["bottom"].set_color(GRID)
            ax.spines["top"].set_color(GRID)
            ax.spines["left"].set_color(GRID)
            ax.spines["right"].set_color(GRID)

        width = 0.7
        for i in range(n):
            oi, hi, li, ci = _to_float(o[i]), _to_float(h[i]), _to_float(l[i]), _to_float(c[i])
            color = GREEN if ci >= oi else RED
            ax_main.plot([i, i], [li, hi], color=color, linewidth=1.2)
            body_bottom = min(oi, ci)
            body_height = max(abs(ci - oi), y_range * 0.008)
            rect = Rectangle((i - width / 2, body_bottom), width, body_height, facecolor=color, edgecolor=color)
            ax_main.add_patch(rect)

        proj_n = 10
        ind = indicators or {}
        atr_val = _to_float(ind.get("atr", 0)) or (y_range * 0.02)
        support = _to_float(ind.get("support", 0))
        resistance = _to_float(ind.get("resistance", 0))
        proj_o, proj_h, proj_l, proj_c = _project_candles(
            last_close, y_range, atr_val, prediction, proj_n, support, resistance
        )
        proj_color = GREEN if prediction.phase1 == "YUKSELIR" else RED
        for i in range(proj_n):
            oi, hi, li, ci = proj_o[i], proj_h[i], proj_l[i], proj_c[i]
            ax_main.plot([n + i, n + i], [li, hi], color=proj_color, linewidth=1, linestyle="--", alpha=0.6)
            body_bottom = min(oi, ci)
            body_height = max(abs(ci - oi), y_range * 0.005)
            rect = Rectangle((n + i - width / 2, body_bottom), width, body_height,
                             facecolor=proj_color, edgecolor=proj_color, alpha=0.4)
            ax_main.add_patch(rect)

        y_min_all = min(y_min, float(np.min(proj_l)) if len(proj_l) > 0 else y_min)
        y_max_all = max(y_max, float(np.max(proj_h)) if len(proj_h) > 0 else y_max)
        ax_main.set_xlim(-1, n + proj_n + 2)
        ax_main.set_ylim(y_min_all - y_range * 0.03, y_max_all + y_range * 0.08)
        ax_main.axvline(x=n - 0.5, color=GRID, linestyle="--", alpha=0.5)
        ax_main.set_ylabel("Fiyat", fontsize=10, color=TEXT)
        ax_main.grid(True, alpha=0.15, color=GRID, linestyle="-")
        ax_main.set_xticks([])
        ax_main.set_title(f"{symbol}  {interval}", fontsize=12, color=TEXT, pad=8)

        self._draw_arrows(ax_main, n, last_close, y_range, prediction, GREEN, RED)

        vol_colors = [GREEN if _to_float(c[i]) >= _to_float(o[i]) else RED for i in range(n)]
        ax_vol.bar(range(n), v, color=vol_colors, width=0.85, alpha=0.6)
        ax_vol.set_ylabel("Hacim", fontsize=10, color=TEXT)
        ax_vol.set_xlabel("")
        ax_vol.grid(True, alpha=0.15, color=GRID, linestyle="-")
        ax_vol.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=8))
        ax_vol.tick_params(axis="x", labelsize=8)

        fig.tight_layout(pad=1.5)
        self._canvas = FigureCanvas(fig)
        self._chart_layout.addWidget(self._canvas)
        self._canvas.draw()

        self._fill_table(symbol, interval, prediction, last_close, n, indicators)

    def _draw_arrows(self, ax, n: int, last_close: float, y_range: float, pred: PredictionResult, green: str = "#0ecb81", red: str = "#f6465d") -> None:
        x_start, x_end = n - 1, n + 0.5
        def draw(direction: str, x_off: float, color: str):
            xs, xe = x_start + x_off, x_end + x_off
            ye = last_close + (y_range * 0.06 if direction == "YUKSELIR" else -y_range * 0.06)
            ax.annotate("", xy=(xe, ye), xytext=(xs, last_close), arrowprops=dict(arrowstyle="->", color=color, lw=2))
            lbl = "YUKSELIR" if direction == "YUKSELIR" else "ALCALIR"
            ax.text(xe + 0.15, ye, lbl[:6], color=color, fontsize=8, fontweight="bold")

        if pred.phase2:
            draw(pred.phase1, 0, green)
            draw(pred.phase2, 1.2, red)
        else:
            draw(pred.phase1, 0, green if pred.phase1 == "YUKSELIR" else red)

    def _fill_table(
        self,
        symbol: str,
        interval: str,
        pred: PredictionResult,
        last_close: float,
        n: int,
        indicators: Optional[dict],
    ) -> None:
        conf_pct = int(getattr(pred, "confidence", 0.5) * 100)
        rows = [
            ("Parite", symbol),
            ("Dilim", interval),
            ("Tahmin", pred.summary),
            ("Faz 1", pred.phase1),
            ("Faz 2", pred.phase2 or "-"),
            ("Guven", f"%{conf_pct}"),
            ("Son kapanis", f"{last_close:,.4f}"),
            ("Mum sayisi", str(n)),
        ]
        if indicators:
            for k in ("rsi", "trend", "support", "resistance", "adx", "atr"):
                v = indicators.get(k)
                if v is not None:
                    if isinstance(v, (int, float)):
                        rows.append((k.upper(), f"{_to_float(v):,.2f}"))
                    else:
                        rows.append((k.upper(), str(v)))
        self._table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(k)))
            self._table.setItem(i, 1, QTableWidgetItem(str(v)))

    def _fill_table_error(self, symbol: str, interval: str) -> None:
        self._table.setRowCount(2)
        self._table.setItem(0, 0, QTableWidgetItem("Parite"))
        self._table.setItem(0, 1, QTableWidgetItem(symbol))
        self._table.setItem(1, 0, QTableWidgetItem("Durum"))
        self._table.setItem(1, 1, QTableWidgetItem("Veri yetersiz"))

    def _show_error(self, msg: str) -> None:
        fig = Figure(figsize=(12, 6), dpi=100, facecolor="#131722")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#131722")
        ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=14, color="#eaecef", transform=ax.transAxes)
        ax.axis("off")
        self._canvas = FigureCanvas(fig)
        self._chart_layout.addWidget(self._canvas)
        self._canvas.draw()
