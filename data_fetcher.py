import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

# Binance USDT-M Futures (Perpetual) - Kaldirac icin
BASE_URL = "https://fapi.binance.com/fapi/v1"


class TLS12Adapter(HTTPAdapter):
    """TLS 1.2+ zorlayarak SSL WRONG_VERSION_NUMBER hatasini onler."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        try:
            ctx.set_min_proto_version(ssl.TLSVersion.TLSv1_2)
        except AttributeError:
            ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # Proxy/antivirus SSL kesintisini atla
    s.mount("https://", TLS12Adapter())
    return s


_session_instance: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session_instance
    if _session_instance is None:
        _session_instance = _session()
    return _session_instance

INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]

_cached_usdt_symbols: list[str] = []


def fetch_usdt_symbols() -> list[str]:
    global _cached_usdt_symbols
    if _cached_usdt_symbols:
        return _cached_usdt_symbols

    try:
        resp = _get_session().get(f"{BASE_URL}/exchangeInfo", timeout=15)
        resp.raise_for_status()
        info = resp.json()

        symbols = sorted(
            s["symbol"]
            for s in info["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"
        )
        _cached_usdt_symbols = symbols
        return symbols
    except Exception:
        return [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
        ]


def search_symbols(query: str) -> list[str]:
    all_syms = fetch_usdt_symbols()
    if not query:
        return all_syms[:50]
    q = query.upper().strip()
    exact = [s for s in all_syms if s.startswith(q)]
    contains = [s for s in all_syms if q in s and s not in exact]
    return (exact + contains)[:50]


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 500,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> pd.DataFrame:
    params: dict = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": min(limit, 1000),
    }
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time

    last_err = None
    for attempt in range(4):
        try:
            resp = _get_session().get(f"{BASE_URL}/klines", params=params, timeout=20)
            if resp.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        except (requests.RequestException, OSError) as e:
            last_err = e
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_err
    else:
        if last_err:
            raise last_err
        resp.raise_for_status()
    raw = resp.json()

    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")

    for col in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"):
        if col in df.columns:
            df[col] = df[col].astype(float)

    df.set_index("open_time", inplace=True)
    df.index.name = "Date"

    return df


def fetch_multiple_klines_parallel(
    symbols: list[str],
    interval: str = "1h",
    limit: int = 500,
    max_workers: int = 5,
    batch_delay_sec: float = 0.15,
) -> dict[str, pd.DataFrame]:
    """
    Birden fazla sembol icin paralel kline cekme (ThreadPoolExecutor).
    Rate limit: 30+ sembolde batch'ler arasi kisa gecikme.
    Returns: {symbol: DataFrame}
    """
    result: dict[str, pd.DataFrame] = {}
    batch_size = 25 if len(symbols) > 30 else len(symbols)

    def _fetch(sym: str) -> tuple[str, pd.DataFrame]:
        try:
            df = fetch_klines(sym, interval, limit)
            return sym, df
        except Exception as e:
            try:
                from app_logging import get_logger
                get_logger("data_fetcher").warning("fetch_klines %s: %s", sym, e)
            except ImportError:
                pass
            return sym, pd.DataFrame()

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        if i > 0 and batch_delay_sec > 0:
            time.sleep(batch_delay_sec)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as ex:
            futures = {ex.submit(_fetch, s): s for s in batch}
            for fut in as_completed(futures):
                sym, df = fut.result()
                result[sym] = df
    return result


def fetch_ticker_price(symbol: str = "BTCUSDT") -> float:
    resp = _get_session().get(
        f"{BASE_URL}/ticker/price",
        params={"symbol": symbol.upper()},
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def fetch_ticker_24h(symbol: str = "BTCUSDT") -> Optional[dict]:
    """24 saatlik ticker: quoteVolume (USDT), count (islem sayisi)."""
    try:
        resp = _get_session().get(
            f"{BASE_URL}/ticker/24hr",
            params={"symbol": symbol.upper()},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "quoteVolume": float(d.get("quoteVolume", 0)),
            "count": int(d.get("count", 0)),
        }
    except Exception:
        return None


def fetch_funding_rate(symbol: str = "BTCUSDT", limit: int = 1) -> Optional[float]:
    """Binance Futures guncel funding rate (ornegin 0.0001 = %0.01)."""
    try:
        resp = _get_session().get(
            f"{BASE_URL}/fundingRate",
            params={"symbol": symbol.upper(), "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list):
            return float(data[-1]["fundingRate"])
        if data and isinstance(data, dict) and "fundingRate" in data:
            return float(data["fundingRate"])
        return None
    except Exception:
        return None


def fetch_funding_rate_history(symbol: str = "BTCUSDT", limit: int = 24) -> list[dict]:
    """Son N funding rate - trend analizi. limit=24 = son 8 gun (8h aralik)."""
    try:
        resp = _get_session().get(
            f"{BASE_URL}/fundingRate",
            params={"symbol": symbol.upper(), "limit": min(limit, 100)},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list):
            return [{"rate": float(d["fundingRate"]), "time": d.get("fundingTime")} for d in data]
        return []
    except Exception:
        return []


def fetch_open_interest(symbol: str = "BTCUSDT") -> Optional[float]:
    """Binance Futures anlik open interest (kontrat sayisi)."""
    try:
        resp = _get_session().get(
            f"{BASE_URL}/openInterest",
            params={"symbol": symbol.upper()},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("openInterest", 0))
    except Exception:
        return None


def fetch_prev_day_high_low(symbol: str = "BTCUSDT") -> Optional[dict]:
    """Onceki gun (1d) high ve low."""
    try:
        df = fetch_klines(symbol, "1d", 3)
        if len(df) < 2:
            return None
        prev = df.iloc[-2]
        return {"high": float(prev["high"]), "low": float(prev["low"]), "close": float(prev["close"])}
    except Exception:
        return None


def fetch_fear_greed() -> Optional[dict]:
    """Alternative.me Fear & Greed Index (0-100)."""
    try:
        resp = _get_session().get("https://api.alternative.me/fng/?limit=1", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("data") and len(data["data"]) > 0:
            d = data["data"][0]
            return {
                "value": int(d.get("value", 0)),
                "classification": d.get("value_classification", "Unknown"),
                "timestamp": d.get("timestamp"),
            }
    except Exception:
        pass
    return None


def fetch_btc_dominance() -> Optional[float]:
    """CoinGecko global - BTC market cap dominance %."""
    try:
        resp = _get_session().get("https://api.coingecko.com/api/v3/global", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        mc = data.get("data", {}).get("market_cap_percentage", {})
        return float(mc.get("btc", 0))
    except Exception:
        pass
    return None


def fetch_order_book(symbol: str = "BTCUSDT", limit: int = 20) -> Optional[dict]:
    """Order book: bids/asks [price, qty]. Imbalance hesaplamak icin."""
    try:
        resp = _get_session().get(
            f"{BASE_URL}/depth",
            params={"symbol": symbol.upper(), "limit": min(limit, 100)},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        bids = [[float(p), float(q)] for p, q in data.get("bids", [])[:limit]]
        asks = [[float(p), float(q)] for p, q in data.get("asks", [])[:limit]]
        return {"bids": bids, "asks": asks}
    except Exception:
        return None


def fetch_order_book_imbalance(symbol: str = "BTCUSDT", depth: int = 20) -> Optional[dict]:
    """
    Bid/ask hacim dengesi. LONG icin bid > ask = alim baskisi.
    Returns: {imbalance: -1..1, bid_vol, ask_vol, spread_bps, best_bid, best_ask}
    """
    ob = fetch_order_book(symbol, depth)
    if not ob or not ob["bids"] or not ob["asks"]:
        return None
    bid_vol = sum(q for _, q in ob["bids"])
    ask_vol = sum(q for _, q in ob["asks"])
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0
    best_bid = ob["bids"][0][0]
    best_ask = ob["asks"][0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10000 if mid > 0 else 0
    return {
        "imbalance": imbalance,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "spread_bps": spread_bps,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid,
    }


# Kline cache: (symbol, interval, limit) -> (df, timestamp). TTL saniye.
_klines_cache: dict[tuple, tuple[pd.DataFrame, float]] = {}
_klines_cache_max_size = 50
_klines_cache_ttl: dict[str, int] = {"1m": 30, "5m": 60, "15m": 120, "1h": 300, "4h": 600, "1d": 900}


def safe_fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 500,
    retries: int = 3,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Kline cek. use_cache=True ile son TTL icinde cache'den doner."""
    key = (symbol.upper(), interval, limit)
    ttl = _klines_cache_ttl.get(interval, 120)
    now = time.time()
    if use_cache and key in _klines_cache:
        df, ts = _klines_cache[key]
        if now - ts < ttl and not df.empty:
            return df.copy()
    for attempt in range(retries):
        try:
            df = fetch_klines(symbol, interval, limit)
            if use_cache and not df.empty:
                if len(_klines_cache) >= _klines_cache_max_size:
                    oldest = min(_klines_cache.items(), key=lambda x: x[1][1])
                    del _klines_cache[oldest[0]]
                _klines_cache[key] = (df.copy(), now)
            return df
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 ** attempt)
    return pd.DataFrame()


def fetch_liquidations(symbol: str = "BTCUSDT", limit: int = 50) -> Optional[dict]:
    """
    Son zorla kapatma (liquidation) kayitlari.
    Binance REST'te sembol bazli force orders icin genelde auth gerekir;
    ozet donulur, veri yoksa None veya bos ozet.
    """
    try:
        resp = _get_session().get(
            f"{BASE_URL}/forceOrders",
            params={"symbol": symbol.upper(), "limit": min(limit, 100)},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"long_liq_usd": 0, "short_liq_usd": 0, "count": 0, "note": "REST sinirli"}
        data = resp.json()
        if not isinstance(data, list):
            return {"long_liq_usd": 0, "short_liq_usd": 0, "count": 0, "note": "Veri yok"}
        long_liq, short_liq = 0.0, 0.0
        for o in data:
            side = (o.get("side") or "").upper()
            qty = float(o.get("origQty") or 0)
            price = float(o.get("price") or 0)
            usd = qty * price
            if side == "BUY":
                short_liq += usd
            else:
                long_liq += usd
        return {"long_liq_usd": round(long_liq, 0), "short_liq_usd": round(short_liq, 0), "count": len(data)}
    except Exception:
        return {"long_liq_usd": 0, "short_liq_usd": 0, "count": 0, "note": "Hata"}


def fetch_exchange_flow_signal() -> Optional[str]:
    """
    Borsa giris/cikis yonu (on-chain/whale basit).
    Ucretsiz public API sinirli; Glassnode/CryptoQuant API key ile doldurulabilir.
    Donus: 'inflow' | 'outflow' | None
    """
    return None
