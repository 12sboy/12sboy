from __future__ import annotations

import json
import os
import time as time_module
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time, timezone
from pathlib import Path

import yaml

from .backtest import BacktestEngine
from .core import PBInvestingStrategy, Position, StrategyConfig
from .exchange import _is_transient_network_error, load_exchange
from .help import TELEGRAM_BOT_COMMANDS, telegram_help
from .notifier import Notifier, TelegramNotifier, make_notifier
from .reporting import format_hourly_report, format_trade_message, market_snapshot
from .risk import RiskConfig, calculate_order_qty
from .scheduler import HourlyGate


@dataclass(frozen=True)
class BotConfig:
    symbols: list[str]
    timeframe: str
    mode: str
    equity_usdt: float
    strategy: StrategyConfig
    risk: RiskConfig
    order_type: str = "Limit"
    loop_interval_sec: int = 60
    hourly_report: bool = True
    telegram_token: str | None = None
    telegram_chat_id: str | None = None


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text())
    telegram = raw.get("telegram", {}) or {}
    return BotConfig(
        symbols=raw.get("symbols", ["BTC/USDT", "ETH/USDT"]),
        timeframe=raw.get("timeframe", "5m"),
        mode=raw.get("mode", "paper"),
        equity_usdt=float(raw.get("equity_usdt", 1000.0)),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        order_type=raw.get("order_type", "Limit"),
        loop_interval_sec=int(raw.get("loop_interval_sec", 60)),
        hourly_report=bool(raw.get("hourly_report", True)),
        telegram_token=telegram.get("token") or os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=str(telegram.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or "") or None,
    )


def derive_crypto_levels(candles) -> dict[str, float]:
    """Derive crypto-friendly levels from 24/7 data.

    - prior_high/low: previous UTC day high/low
    - pre_high/low: current UTC day 00:00-08:00 high/low, falling back to first 12 candles
    """
    if not candles:
        raise ValueError("candles required")
    last_day = candles[-1].timestamp.date()
    prior_days = [c for c in candles if c.timestamp.date() < last_day]
    current = [c for c in candles if c.timestamp.date() == last_day]
    pre = [c for c in current if time(0, 0) <= c.timestamp.time() < time(8, 0)] or current[:12] or candles[:12]
    prior = prior_days or candles[: max(1, len(candles) // 2)]
    return {
        "pre_high": max(c.high for c in pre),
        "pre_low": min(c.low for c in pre),
        "prior_high": max(c.high for c in prior),
        "prior_low": min(c.low for c in prior),
    }


class TradingBot:
    def __init__(self, config: BotConfig, notifier: Notifier | None = None):
        self.config = config
        self.strategy = PBInvestingStrategy(config.strategy)
        self.exchange = load_exchange(config.mode, cash_usdt=config.equity_usdt)
        self.notifier = notifier or make_notifier(config.telegram_token, config.telegram_chat_id)
        self.positions: dict[str, Position] = {}
        self.hourly_gate = HourlyGate()
        self._leverage_configured = False
        self._telegram_update_offset: int | None = None
        self._telegram_offset_path = Path("reports/telegram_update_offset.txt")
        self._last_entry_candle: dict[str, tuple[datetime, str, str | None]] = {}
        self._last_error_notice_at: datetime | None = None
        self._last_error_notice_text: str | None = None

    def _notify_safe(self, text: str) -> bool:
        try:
            self.notifier.send(text)
            return True
        except Exception as exc:
            print(f"notification failed: {type(exc).__name__}: {exc}", flush=True)
            return False

    def _notify_error(self, exc: Exception) -> None:
        now = datetime.now(timezone.utc)
        if _is_transient_network_error(exc):
            text = f"네트워크 일시 오류: {type(exc).__name__}: {exc} — 자동 재시도/다음 루프 계속"
            # DNS/timeout glitches can happen on Termux mobile networks. Log every
            # occurrence locally but do not spam Telegram more than once per hour.
            print(text, flush=True)
            if self._last_error_notice_at and now - self._last_error_notice_at < timedelta(hours=1):
                return
        else:
            text = f"봇 오류: {type(exc).__name__}: {exc}"
            if self._last_error_notice_text == text and self._last_error_notice_at and now - self._last_error_notice_at < timedelta(minutes=10):
                print(text, flush=True)
                return
        if self._notify_safe(text):
            self._last_error_notice_at = now
            self._last_error_notice_text = text

    def _sync_exchange_position(self, symbol: str) -> Position | None:
        fetch_positions = getattr(self.exchange, "fetch_positions", None)
        if not callable(fetch_positions):
            return self.positions.get(symbol)
        active: list[Position] = []
        for raw in fetch_positions(symbol):
            size = float(raw.get("size") or 0)
            if size <= 0:
                continue
            idx = int(raw.get("positionIdx") or 0)
            side_text = (raw.get("side") or "").lower()
            if idx == 1 or side_text == "buy":
                side = "LONG"
            elif idx == 2 or side_text == "sell":
                side = "SHORT"
            else:
                continue
            entry = float(raw.get("avgPrice") or raw.get("sessionAvgPrice") or 0)
            if entry > 0:
                active.append(Position(symbol, side, size, entry))
        if active:
            self.positions[symbol] = active[0]
            return active[0]
        self.positions.pop(symbol, None)
        return None

    def _fetch_open_orders(self, symbol: str) -> list[dict]:
        fetch_open_orders = getattr(self.exchange, "fetch_open_orders", None)
        if not callable(fetch_open_orders):
            return []
        return fetch_open_orders(symbol)

    def _cancel_stale_open_orders(self, symbol: str, open_orders: list[dict] | None = None, max_age_sec: int = 600) -> int:
        cancel_all_orders = getattr(self.exchange, "cancel_all_orders", None)
        if not callable(cancel_all_orders):
            return 0
        open_orders = self._fetch_open_orders(symbol) if open_orders is None else open_orders
        if not open_orders:
            return 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        stale_orders = []
        for order in open_orders:
            try:
                created_ms = int(order.get("createdTime") or order.get("updatedTime") or now_ms)
            except (TypeError, ValueError):
                created_ms = now_ms
            if now_ms - created_ms >= max_age_sec * 1000:
                stale_orders.append(order)
        if not stale_orders:
            return 0
        cancel_all_orders(symbol)
        self._notify_safe(f"{symbol} 오래된 미체결 주문 {len(open_orders)}개 취소: {max_age_sec//60}분 이상 미체결")
        return len(open_orders)

    def configure_futures_leverage(self) -> None:
        if self._leverage_configured:
            return
        setter = getattr(self.exchange, "set_leverage", None)
        if callable(setter):
            for symbol in self.config.symbols:
                setter(symbol, self.config.risk.leverage)
        self._leverage_configured = True

    def run_once(self) -> list[dict]:
        self.configure_futures_leverage()
        events = []
        for symbol in self.config.symbols:
            candles = self.exchange.fetch_ohlcv(symbol, self.config.timeframe, limit=200)
            candles = completed_candles(candles, self.config.timeframe)
            if len(candles) < 2:
                continue
            enriched = self.strategy.compute_indicators(candles)
            cur = enriched[-1]
            levels = derive_crypto_levels(candles)
            position = self._sync_exchange_position(symbol)
            if position:
                signal = self.strategy.manage_position(position, cur, ema8=cur.ema8 or cur.close, vwap=cur.vwap or cur.close)
                if signal.action in ("TAKE_PROFIT", "CLOSE"):
                    qty = position.qty * signal.qty_fraction if signal.action == "TAKE_PROFIT" else position.qty
                    order = self.exchange.place_order(symbol, _close_side(position.side), qty, order_type="Market", reduce_only=True, position_idx=_position_idx_for_position(position.side))
                    events.append({"symbol": symbol, "signal": asdict(signal), "order": order})
                    self._notify_safe(format_trade_message(events[-1]))
                    if signal.action == "TAKE_PROFIT":
                        position.qty -= qty
                        position.scaled_out = True
                    else:
                        self.positions.pop(symbol, None)
                continue

            open_orders = self._fetch_open_orders(symbol)
            if open_orders and self._cancel_stale_open_orders(symbol, open_orders):
                open_orders = []

            signal = self.strategy.on_candle(candles, symbol, levels)
            if signal.action in ("BUY", "SELL") and signal.side:
                signature = (cur.timestamp, signal.action, signal.side)
                if self._last_entry_candle.get(symbol) == signature:
                    events.append({"symbol": symbol, "signal": asdict(signal), "skipped": "duplicate_closed_candle_signal", "levels": levels})
                    continue
                if open_orders:
                    events.append({"symbol": symbol, "signal": asdict(signal), "skipped": "open_order_pending", "open_orders": open_orders, "levels": levels})
                    continue
                entry = signal.price or cur.close
                stop = cur.vwap or entry
                qty = calculate_order_qty(self.config.equity_usdt, entry, stop if stop != entry else cur.low, self.config.risk) * signal.position_size_multiplier
                if qty > 0:
                    order = self.exchange.place_order(symbol, "Buy" if signal.side == "LONG" else "Sell", qty, order_type=self.config.order_type, price=entry)
                    self._last_entry_candle[symbol] = signature
                    if self.config.order_type.lower() == "market":
                        self.positions[symbol] = Position(symbol, signal.side, qty, entry)
                    events.append({"symbol": symbol, "signal": asdict(signal), "order": order, "levels": levels})
                    self._notify_safe(format_trade_message(events[-1]))
            else:
                events.append({"symbol": symbol, "signal": asdict(signal), "levels": levels})
        return events

    def build_hourly_report(self) -> str:
        snapshots = {}
        for symbol in self.config.symbols:
            candles = self.exchange.fetch_ohlcv(symbol, self.config.timeframe, limit=12)
            enriched = self.strategy.compute_indicators(candles)
            last = enriched[-1]
            snapshots[symbol] = market_snapshot(candles, vwap=last.vwap, ema8=last.ema8)
        try:
            balance = self.exchange.fetch_balance()
        except Exception as exc:
            balance = {"error": {"equity": f"balance fetch failed: {exc}", "walletBalance": "unknown"}}
        for symbol in self.config.symbols:
            try:
                self._sync_exchange_position(symbol)
            except Exception:
                pass
        return format_hourly_report(snapshots, balance, self.positions)

    def maybe_send_hourly_report(self, now: datetime | None = None) -> bool:
        if not self.config.hourly_report:
            return False
        now = now or datetime.now(timezone.utc)
        if self.hourly_gate.should_fire(now):
            self._notify_safe(self.build_hourly_report())
            return True
        return False

    def setup_telegram_commands(self) -> bool:
        if isinstance(self.notifier, TelegramNotifier):
            self.notifier.set_my_commands(TELEGRAM_BOT_COMMANDS)
            return True
        return False

    def process_telegram_commands_once(self) -> int:
        """Handle safe inbound Telegram commands without placing orders."""
        if not isinstance(self.notifier, TelegramNotifier):
            return 0
        if self._telegram_update_offset is None and self._telegram_offset_path.exists():
            try:
                self._telegram_update_offset = int(self._telegram_offset_path.read_text().strip())
            except ValueError:
                self._telegram_update_offset = None
        updates = self.notifier.get_updates(offset=self._telegram_update_offset, timeout_sec=0)
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                self._telegram_update_offset = int(update_id) + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("id")) != str(self.config.telegram_chat_id):
                continue
            text = (message.get("text") or "").strip()
            command = text.split()[0].split("@", 1)[0].lower() if text else ""
            if command in ("/help", "help", "도움말", "명령어"):
                self._notify_safe(telegram_help())
                handled += 1
            elif command in ("/status", "status", "상태", "리포트"):
                self._notify_safe(self.build_hourly_report())
                handled += 1
        if self._telegram_update_offset is not None:
            self._telegram_offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._telegram_offset_path.write_text(str(self._telegram_update_offset))
        return handled

    def run_forever(self) -> None:
        try:
            self.setup_telegram_commands()
        except Exception as exc:
            self._notify_safe(f"텔레그램 명령어 등록 실패: {type(exc).__name__}: {exc}")
        self._notify_safe(f"봇 시작: mode={self.config.mode}, symbols={', '.join(self.config.symbols)}, timeframe={self.config.timeframe}. 텔레그램 /help 사용 가능")
        while True:
            try:
                self.process_telegram_commands_once()
                self.run_once()
                self.maybe_send_hourly_report()
            except Exception as exc:
                self._notify_error(exc)
            time_module.sleep(self.config.loop_interval_sec)


def run_backtest_from_public(config: BotConfig) -> dict:
    exchange = load_exchange("public")
    reports = {}
    for symbol in config.symbols:
        candles = exchange.fetch_ohlcv(symbol, config.timeframe, limit=200)
        levels = derive_crypto_levels(candles)
        reports[symbol] = BacktestEngine(config.equity_usdt, config.strategy, config.risk).run(candles, symbol, levels)
    return reports


def _close_side(side: str) -> str:
    return "Sell" if side == "LONG" else "Buy"


def timeframe_delta(timeframe: str) -> timedelta:
    value = timeframe.strip().lower()
    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))
    if value.endswith("d"):
        return timedelta(days=int(value[:-1]))
    return timedelta(minutes=int(value))


def completed_candles(candles, timeframe: str, now: datetime | None = None):
    if not candles:
        return candles
    now = now or datetime.now(timezone.utc)
    delta = timeframe_delta(timeframe)
    if candles[-1].timestamp + delta > now:
        return candles[:-1]
    return candles


def _position_idx_for_position(side: str) -> int:
    if side == "LONG":
        return 1
    if side == "SHORT":
        return 2
    raise ValueError(f"unsupported position side: {side}")
