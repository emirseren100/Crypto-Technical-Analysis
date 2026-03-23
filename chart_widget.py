import matplotlib
matplotlib.use("Agg")

import mplfinance as mpf
import pandas as pd
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QVBoxLayout, QWidget

from indicators import compute_all
from price_action import compute_pivot_points, find_support_resistance
from signal_engine import TradeSetup
from theme import GREEN, RED


class ChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas: FigureCanvas | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def plot(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        interval: str = "",
        setup: TradeSetup | None = None,
        scalp: bool = False,
        figsize: tuple[float, float] = (12, 8),
    ) -> None:
        if self._canvas:
            self._layout.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas.figure.clear()
            self._canvas = None

        if df.empty or len(df) < 30:
            from matplotlib.figure import Figure
            fig = Figure(figsize=figsize, dpi=100, facecolor="#131722")
            ax = fig.add_subplot(111)
            ax.set_facecolor("#131722")
            ax.text(0.5, 0.5, "Yetersiz veri", ha="center", va="center", fontsize=14, color="#eaecef")
            ax.axis("off")
            self._canvas = FigureCanvas(fig)
            self._layout.addWidget(self._canvas)
            self._canvas.draw()
            return

        data = compute_all(df, scalp=scalp)
        ohlcv = data[["open", "high", "low", "close", "volume"]].copy()
        ohlcv.columns = ["Open", "High", "Low", "Close", "Volume"]

        add_plots = self._build_addplots(data)
        sr_lines = self._build_sr_lines(df, setup)

        mc = mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit")
        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=mc,
            facecolor="#131722",
            figcolor="#131722",
            gridcolor="#2b2f3a",
            gridstyle="-",
            rc={"font.size": 8, "axes.facecolor": "#131722", "figure.facecolor": "#131722", "axes.edgecolor": "#2b2f3a", "xtick.color": "#848e9c", "ytick.color": "#848e9c"},
        )

        fig, axes = mpf.plot(
            ohlcv,
            type="candle",
            style=style,
            addplot=add_plots,
            volume=True,
            volume_panel=2,
            hlines=sr_lines,
            title=f"\n{symbol}  {interval}" if symbol else "",
            figsize=figsize,
            panel_ratios=(4, 1.2, 1, 1.2),
            returnfig=True,
            warn_too_much_data=2000,
        )

        self._canvas = FigureCanvas(fig)
        self._layout.addWidget(self._canvas)
        self._canvas.draw()

    def save_to_png(
        self,
        filepath: str | Path | None = None,
        symbol: str = "",
        interval: str = "",
        setup: TradeSetup | None = None,
        dpi: int = 150,
    ) -> str | None:
        """
        Grafik + setup bilgisi PNG olarak kaydet.
        filepath None ise: symbol_interval_tarih.png
        Returns: kaydedilen dosya yolu veya None
        """
        if not self._canvas or not hasattr(self._canvas, "figure"):
            return None
        fig = self._canvas.figure
        txt_obj = None
        if setup:
            from format_utils import price_precision
            prec = price_precision(setup.entry)
            info = (
                f"{setup.direction} | Entry: {setup.entry:.{prec}f} | SL: {setup.stop_loss:.{prec}f} | "
                f"TP1: {setup.tp1:.{prec}f} TP2: {setup.tp2:.{prec}f} TP3: {setup.tp3:.{prec}f} | Guven: {setup.confidence}/10"
            )
            txt_obj = fig.text(0.5, 0.01, info, ha="center", fontsize=8, color="#9e9e9e", wrap=True)
        if filepath is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            name = f"{symbol}_{interval}_{ts}.png" if symbol else f"chart_{ts}.png"
            filepath = Path.cwd() / name
        else:
            filepath = Path(filepath)
        try:
            fig.savefig(str(filepath), dpi=dpi, facecolor="#131722", edgecolor="none", bbox_inches="tight")
            if txt_obj is not None:
                txt_obj.remove()
            return str(filepath)
        except Exception:
            if txt_obj is not None:
                try:
                    txt_obj.remove()
                except Exception:
                    pass
            return None

    # ------------------------------------------------------------------

    def _build_addplots(self, data: pd.DataFrame) -> list:
        plots = []
        _ap = mpf.make_addplot

        if data["sma_9"].notna().any():
            plots.append(_ap(data["sma_9"], color="#ffeb3b", width=0.8, panel=0))
        if data["sma_21"].notna().any():
            plots.append(_ap(data["sma_21"], color="#2196f3", width=0.8, panel=0))
        if data["ema_50"].notna().any():
            plots.append(_ap(data["ema_50"], color="#ff9800", width=0.8, panel=0))
        if data["ema_200"].notna().any():
            plots.append(_ap(data["ema_200"], color="#9c27b0", width=0.6, panel=0))

        if data["bb_upper"].notna().any():
            plots.append(_ap(data["bb_upper"], color="#9e9e9e", width=0.6, linestyle="--", panel=0))
            plots.append(_ap(data["bb_lower"], color="#9e9e9e", width=0.6, linestyle="--", panel=0))

        if data["rsi"].notna().any():
            plots.append(_ap(data["rsi"], panel=1, color="#e91e63", ylabel="RSI", width=0.9))
            rsi_70 = pd.Series(70, index=data.index, dtype=float)
            rsi_30 = pd.Series(30, index=data.index, dtype=float)
            plots.append(_ap(rsi_70, panel=1, color="#ffffff", width=0.4, linestyle="--"))
            plots.append(_ap(rsi_30, panel=1, color="#ffffff", width=0.4, linestyle="--"))

        if data["macd"].notna().any():
            plots.append(_ap(data["macd"], panel=3, color="#26c6da", ylabel="MACD", width=0.8))
            plots.append(_ap(data["macd_signal"], panel=3, color="#ffa726", width=0.8))

            hist = data["macd_hist"]
            hist_colors = [GREEN if (pd.notna(v) and float(v) >= 0) else RED for v in hist]
            plots.append(_ap(hist, type="bar", panel=3, color=hist_colors, width=0.7))

        return plots

    def _build_sr_lines(self, df: pd.DataFrame, setup: TradeSetup | None = None) -> dict:
        levels = find_support_resistance(df)
        prices: list[float] = []
        colors: list[str] = []

        if setup:
            prices.extend([setup.entry, setup.stop_loss, setup.tp1, setup.tp2, setup.tp3])
            colors.extend([
                "#eaecef",   # Entry
                RED,         # SL
                GREEN,       # TP1
                GREEN,       # TP2
                GREEN,       # TP3
            ])

        pivots = compute_pivot_points(df)
        if pivots:
            for key in ["s2", "s1", "pivot", "r1", "r2"]:
                if key in pivots:
                    prices.append(pivots[key])
                    colors.append("#848e9c" if key == "pivot" else (GREEN if key.startswith("s") else RED))

        for lv in levels or []:
            prices.append(lv.price)
            colors.append(GREEN if lv.kind == "support" else RED)

        if not prices:
            return dict(hlines=[], colors=[], linestyle="-.", linewidths=0.6)
        n = len(prices)
        lw = (1.4,) * min(5, n) + (0.7,) * max(0, n - 5) if setup else (0.8,) * n
        return dict(hlines=prices, colors=colors, linestyle="-.", linewidths=lw)
