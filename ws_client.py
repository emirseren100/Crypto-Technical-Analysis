import json
import ssl
import threading
from typing import Callable, Optional

import websocket

# Binance USDT-M Futures (Perpetual) WebSocket
BINANCE_WS = "wss://fstream.binance.com/ws"


def _ws_ssl_context() -> ssl.SSLContext:
    """TLS 1.2+ ile SSL baglantisi - WRONG_VERSION_NUMBER hatasini onler."""
    ctx = ssl.create_default_context()
    try:
        ctx.set_min_proto_version(ssl.TLSVersion.TLSv1_2)
    except AttributeError:
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    return ctx


class BinanceWebSocket:
    def __init__(self):
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._symbol = ""
        self._interval = ""
        self._on_kline: Optional[Callable] = None
        self._on_ticker: Optional[Callable] = None
        self._on_status: Optional[Callable] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def connect(
        self,
        symbol: str,
        interval: str,
        on_kline: Optional[Callable] = None,
        on_ticker: Optional[Callable] = None,
        on_status: Optional[Callable] = None,
    ) -> None:
        self.disconnect()

        self._symbol = symbol.lower()
        self._interval = interval
        self._on_kline = on_kline
        self._on_ticker = on_ticker
        self._on_status = on_status

        stream_list = f"{self._symbol}@kline_{self._interval}/{self._symbol}@markPrice@1s"
        url = f"wss://fstream.binance.com/stream?streams={stream_list}"

        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._handle_open,
            on_message=self._handle_message,
            on_error=self._handle_error,
            on_close=self._handle_close,
        )

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _run(self) -> None:
        try:
            if self._ws:
                sslopt = {"context": _ws_ssl_context()}
                self._ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    sslopt=sslopt,
                )
        except Exception:
            pass
        finally:
            self._running = False

    def _handle_open(self, ws) -> None:
        self._emit_status("Baglandi")

    def _handle_message(self, ws, message: str) -> None:
        try:
            raw = json.loads(message)
            data = raw.get("data", raw)
        except json.JSONDecodeError:
            return

        event = data.get("e")

        if event == "kline" and self._on_kline:
            k = data["k"]
            kline_data = {
                "time": k["t"],
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "is_closed": k["x"],
            }
            self._on_kline(kline_data)

        elif event == "markPriceUpdate" and self._on_ticker:
            ticker_data = {
                "symbol": data["s"],
                "close": float(data["p"]),
                "open": float(data["p"]),
                "high": float(data["p"]),
                "low": float(data["p"]),
                "volume": 0.0,
            }
            self._on_ticker(ticker_data)

    def _handle_error(self, ws, error) -> None:
        self._emit_status(f"WS Hata: {error}")

    def _handle_close(self, ws, close_status_code, close_msg) -> None:
        self._running = False
        self._emit_status("Baglanti kesildi")

    def _emit_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
