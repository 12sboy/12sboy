from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Protocol

from .core import Candle


class ExchangeClient(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[Candle]: ...
    def place_order(self, symbol: str, side: str, qty: float, order_type: str = "Market", price: float | None = None, reduce_only: bool = False) -> dict: ...
    def fetch_balance(self) -> dict: ...


@dataclass
class PaperExchange:
    """Safe exchange simulator. No real orders are sent."""

    cash_usdt: float = 1000.0

    def __post_init__(self) -> None:
        self.orders: list[dict] = []

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[Candle]:
        return BybitPublicClient().fetch_ohlcv(symbol, timeframe, limit)

    def place_order(self, symbol: str, side: str, qty: float, order_type: str = "Market", price: float | None = None, reduce_only: bool = False) -> dict:
        order = {"id": f"paper-{len(self.orders)+1}", "symbol": symbol, "side": side, "qty": qty, "type": order_type, "price": price, "reduce_only": reduce_only, "timestamp": datetime.now(timezone.utc).isoformat()}
        self.orders.append(order)
        return order

    def fetch_balance(self) -> dict:
        return {"USDT": {"equity": float(self.cash_usdt), "walletBalance": float(self.cash_usdt)}}


class BybitPublicClient:
    base_url = "https://api.bybit.com"

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[Candle]:
        interval = timeframe.rstrip("m") if timeframe.endswith("m") else timeframe
        bybit_symbol = normalize_bybit_symbol(symbol)
        params = urllib.parse.urlencode({"category": "linear", "symbol": bybit_symbol, "interval": interval, "limit": str(limit)})
        url = f"{self.base_url}/v5/market/kline?{params}"
        with _urlopen_with_retry(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {payload}")
        rows = payload["result"]["list"]
        candles = []
        for row in reversed(rows):
            ts_ms, open_, high, low, close, volume, *_ = row
            candles.append(Candle(datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc), float(open_), float(high), float(low), float(close), float(volume)))
        return candles

    def place_order(self, *args, **kwargs) -> dict:
        raise RuntimeError("BybitPublicClient is market-data only. Use PaperExchange or implement authenticated trading after API-key setup.")


class BybitDemoClient(BybitPublicClient):
    """Bybit v5 authenticated client for Demo Trading.

    Endpoint modes:
    - demo: https://api-demo.bybit.com (Bybit Demo Trading)
    - testnet: https://api-testnet.bybit.com
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = "https://api-demo.bybit.com",
        recv_window: str = "20000",
        clock: Callable[[], int] | None = None,
    ):
        self.api_key = api_key or os.environ.get("BYBIT_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BYBIT_API_SECRET", "")
        self.base_url = base_url
        self.recv_window = recv_window
        self._external_clock = clock is not None
        self.clock = clock or (lambda: int(time.time() * 1000))
        self._server_time_offset_ms: int | None = None
        self._instrument_cache: dict[str, dict] = {}
        if not self.api_key or not self.api_secret:
            raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET are required for BybitDemoClient")

    def sign(self, payload: str) -> str:
        raw = f"{self.clock()}{self.api_key}{self.recv_window}{payload}"
        return hmac.new(self.api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def _timestamp(self) -> int:
        if self._external_clock:
            return self.clock()
        if self._server_time_offset_ms is None:
            request = urllib.request.Request(f"{self.base_url}/v5/market/time", method="GET")
            with _urlopen_with_retry(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("retCode") == 0:
                server_ms = int(result.get("time") or int(result["result"]["timeSecond"]) * 1000)
                self._server_time_offset_ms = server_ms - self.clock()
            else:
                self._server_time_offset_ms = 0
        return self.clock() + self._server_time_offset_ms

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: str | None = None,
        private: bool = False,
        ok_ret_codes: set[int] | None = None,
    ) -> dict:
        method = method.upper()
        payload = json.dumps(body or {}, separators=(",", ":")) if method != "GET" else (query or "")
        url = f"{self.base_url}{path}"
        if method == "GET" and query:
            url = f"{url}?{query}"
        data = None if method == "GET" else payload.encode()
        headers = {"Content-Type": "application/json"}
        if private:
            timestamp = str(self._timestamp())
            sign_payload = f"{timestamp}{self.api_key}{self.recv_window}{payload}"
            headers.update(
                {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": self.recv_window,
                    "X-BAPI-SIGN": hmac.new(self.api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest(),
                }
            )
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with _urlopen_with_retry(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        accepted = {0, *(ok_ret_codes or set())}
        if int(result.get("retCode", -1)) not in accepted:
            raise RuntimeError(f"Bybit API error: {result}")
        return result

    def fetch_instrument_info(self, symbol: str) -> dict:
        bybit_symbol = normalize_bybit_symbol(symbol)
        if bybit_symbol in self._instrument_cache:
            return self._instrument_cache[bybit_symbol]
        query = urllib.parse.urlencode({"category": "linear", "symbol": bybit_symbol})
        response = self._request("GET", "/v5/market/instruments-info", query=query, private=False)
        instruments = response.get("result", {}).get("list", [])
        if not instruments:
            raise RuntimeError(f"Bybit instrument not found: {symbol}")
        self._instrument_cache[bybit_symbol] = instruments[0]
        return instruments[0]

    def normalize_order_values(self, symbol: str, qty: float, price: float | None = None) -> tuple[str, str | None]:
        instrument = self.fetch_instrument_info(symbol)
        lot = instrument.get("lotSizeFilter", {})
        price_filter = instrument.get("priceFilter", {})
        qty_step = Decimal(str(lot.get("qtyStep", "0.001")))
        min_qty = Decimal(str(lot.get("minOrderQty", "0")))
        min_notional = Decimal(str(lot.get("minNotionalValue", "0")))
        qty_decimal = _floor_to_step(Decimal(str(qty)), qty_step)
        if qty_decimal < min_qty:
            raise ValueError(f"order qty below Bybit minimum for {symbol}: qty={qty_decimal}, min={min_qty}, step={qty_step}")
        price_text: str | None = None
        if price is not None:
            tick = Decimal(str(price_filter.get("tickSize", "0.01")))
            price_decimal = _floor_to_step(Decimal(str(price)), tick)
            price_text = _fmt_decimal(price_decimal)
            if min_notional and qty_decimal * price_decimal < min_notional:
                raise ValueError(f"order notional below Bybit minimum for {symbol}: notional={qty_decimal * price_decimal}, min={min_notional}")
        return _fmt_decimal(qty_decimal), price_text

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: float | None = None,
        reduce_only: bool = False,
        position_idx: int | None = None,
    ) -> dict:
        qty_text, price_text = self.normalize_order_values(symbol, qty, price)
        body = {
            "category": "linear",
            "symbol": normalize_bybit_symbol(symbol),
            "side": side,
            "orderType": order_type,
            "qty": qty_text,
            "reduceOnly": bool(reduce_only),
            "positionIdx": position_idx if position_idx is not None else _infer_bybit_position_idx(side),
        }
        if order_type == "Limit":
            if price_text is None:
                raise ValueError("price is required for Limit orders")
            body.update({"price": price_text, "timeInForce": "PostOnly"})
        response = self._request("POST", "/v5/order/create", body=body, private=True)
        result = response.get("result", response)
        if isinstance(result, dict):
            result.setdefault("qty", qty_text)
            result.setdefault("price", price_text or _fmt(price or 0))
            result.setdefault("side", side)
            result.setdefault("orderType", order_type)
            result.setdefault("positionIdx", body.get("positionIdx"))
            result.setdefault("reduceOnly", bool(reduce_only))
        return result

    def set_leverage(self, symbol: str, leverage: float) -> dict:
        body = {
            "category": "linear",
            "symbol": normalize_bybit_symbol(symbol),
            "buyLeverage": _fmt(leverage),
            "sellLeverage": _fmt(leverage),
        }
        response = self._request("POST", "/v5/position/set-leverage", body=body, private=True, ok_ret_codes={110043})
        result = response.get("result", response)
        if response.get("retCode") == 110043:
            return {"already_set": True, **result}
        return result

    def fetch_positions(self, symbol: str) -> list[dict]:
        query = urllib.parse.urlencode({"category": "linear", "symbol": normalize_bybit_symbol(symbol)})
        response = self._request("GET", "/v5/position/list", query=query, private=True)
        return response.get("result", {}).get("list", [])

    def fetch_open_orders(self, symbol: str) -> list[dict]:
        query = urllib.parse.urlencode({"category": "linear", "symbol": normalize_bybit_symbol(symbol), "openOnly": "0"})
        response = self._request("GET", "/v5/order/realtime", query=query, private=True)
        return response.get("result", {}).get("list", [])

    def fetch_executions(self, symbol: str, limit: int = 10) -> list[dict]:
        query = urllib.parse.urlencode({"category": "linear", "symbol": normalize_bybit_symbol(symbol), "limit": str(limit)})
        response = self._request("GET", "/v5/execution/list", query=query, private=True)
        return response.get("result", {}).get("list", [])

    def fetch_closed_pnl(self, symbol: str, limit: int = 10) -> list[dict]:
        query = urllib.parse.urlencode({"category": "linear", "symbol": normalize_bybit_symbol(symbol), "limit": str(limit)})
        response = self._request("GET", "/v5/position/closed-pnl", query=query, private=True)
        return response.get("result", {}).get("list", [])

    def cancel_all_orders(self, symbol: str) -> dict:
        body = {"category": "linear", "symbol": normalize_bybit_symbol(symbol)}
        response = self._request("POST", "/v5/order/cancel-all", body=body, private=True, ok_ret_codes={110001})
        return response.get("result", response)

    def fetch_balance(self) -> dict:
        query = urllib.parse.urlencode({"accountType": "UNIFIED", "coin": "USDT"})
        response = self._request("GET", "/v5/account/wallet-balance", query=query, private=True)
        balances: dict[str, dict[str, float]] = {}
        for account in response.get("result", {}).get("list", []):
            for coin in account.get("coin", []):
                balances[coin["coin"]] = {
                    "equity": float(coin.get("equity") or 0),
                    "walletBalance": float(coin.get("walletBalance") or 0),
                    "availableToWithdraw": float(coin.get("availableToWithdraw") or 0),
                }
        return balances


def _fmt(value: float) -> str:
    return _fmt_decimal(Decimal(str(value)))


def _is_transient_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout, OSError))


def _urlopen_with_retry(request, timeout: int = 20, attempts: int = 3):
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except Exception as exc:
            if not _is_transient_network_error(exc) or attempt >= attempts:
                raise
            last_exc = exc
            time.sleep(min(2 ** (attempt - 1), 5))
    if last_exc:
        raise last_exc
    raise RuntimeError("urlopen retry failed unexpectedly")


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class CsvExchange(PaperExchange):
    def __init__(self, csv_path: str, cash_usdt: float = 1000.0):
        super().__init__(cash_usdt)
        self.csv_path = csv_path

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[Candle]:
        candles: list[Candle] = []
        with open(self.csv_path, newline="") as f:
            for row in csv.DictReader(f):
                candles.append(Candle(_parse_ts(row["timestamp"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])))
        return candles[-limit:]


def normalize_bybit_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace(":USDT", "").upper()


def _infer_bybit_position_idx(side: str) -> int:
    """Return hedge-mode positionIdx for Bybit linear orders.

    The user's Demo Trading account is in hedge mode: positionIdx=1 is the
    long leg and positionIdx=2 is the short leg. For new entries, Buy opens
    the long leg and Sell opens the short leg. Reduce-only close orders must
    override this when closing the opposite leg.
    """
    normalized = side.lower()
    if normalized == "buy":
        return 1
    if normalized == "sell":
        return 2
    raise ValueError(f"unsupported Bybit order side: {side}")


def _parse_ts(value: str) -> datetime:
    if value.isdigit():
        raw = int(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_exchange(mode: str = "paper", **kwargs) -> ExchangeClient:
    if mode == "paper":
        return PaperExchange(cash_usdt=float(kwargs.get("cash_usdt", 1000.0)))
    if mode == "public":
        return BybitPublicClient()
    if mode == "csv":
        return CsvExchange(str(kwargs["csv_path"]), cash_usdt=float(kwargs.get("cash_usdt", 1000.0)))
    if mode == "demo":
        return BybitDemoClient(base_url="https://api-demo.bybit.com")
    if mode == "testnet":
        return BybitDemoClient(base_url="https://api-testnet.bybit.com")
    raise ValueError(f"unsupported exchange mode: {mode}")
