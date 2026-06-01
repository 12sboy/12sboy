from __future__ import annotations

"""Telegram monitoring module for crypto trading bots.

Assumptions:
- The trading engine calls ``notify_entry`` / ``notify_exit`` from its order or
  fill callbacks, or pushes equivalent events into this monitor.
- Account/market/report data is provided by a small ``ReportDataProvider``
  adapter implemented by the trading bot. This keeps exchange-specific code out
  of the Telegram layer.
- ``python-telegram-bot`` v20+ is used when installed. For local tests and dry
  runs, ``ConsoleTelegramClient`` can be used without network access.
"""

import asyncio
import html
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol, Sequence

try:  # Optional at import time so unit tests can run without the dependency.
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes
except Exception:  # pragma: no cover - exercised only when dependency missing
    Bot = None  # type: ignore[assignment]
    Update = None  # type: ignore[assignment]
    Application = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    ContextTypes = None  # type: ignore[assignment]


@dataclass(frozen=True)
class TradeEntryEvent:
    symbol: str
    direction: str  # LONG or SHORT
    entry_price: float
    quantity: float
    quantity_unit: str = ""
    leverage: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str | None = None
    source: str = "auto"


@dataclass(frozen=True)
class TradeExitEvent:
    symbol: str
    direction: str
    exit_price: float
    entry_price: float
    quantity: float
    realized_pnl_usdt: float
    realized_pnl_pct: float
    is_stop_loss: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str | None = None
    source: str = "auto"


@dataclass(frozen=True)
class PositionSummary:
    symbol: str
    direction: str
    quantity: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl_usdt: float = 0.0
    unrealized_pnl_pct: float = 0.0


@dataclass(frozen=True)
class AccountSnapshot:
    equity_usdt: float
    wallet_balance_usdt: float | None = None
    unrealized_pnl_usdt: float = 0.0
    unrealized_pnl_pct: float = 0.0
    positions: Sequence[PositionSummary] = ()


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    trend: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PeriodPnlSummary:
    realized_pnl_usdt: float
    realized_pnl_pct: float
    trade_count: int = 0
    long_count: int = 0
    short_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    max_drawdown_usdt: float = 0.0
    max_drawdown_pct: float = 0.0
    stop_loss_total_usdt: float = 0.0
    average_pnl_usdt: float = 0.0
    profit_factor: float | None = None

    @property
    def win_rate_pct(self) -> float:
        return (self.win_count / self.trade_count * 100.0) if self.trade_count else 0.0


@dataclass(frozen=True)
class WeeklySummary(PeriodPnlSummary):
    start_equity_usdt: float = 0.0
    ai_comment: str | None = None


class ReportDataProvider(Protocol):
    async def account_snapshot(self) -> AccountSnapshot: ...
    async def market_snapshots_1h(self) -> Sequence[MarketSnapshot]: ...
    async def realized_pnl_since(self, since: datetime) -> PeriodPnlSummary: ...
    async def daily_summary(self, day: datetime | None = None) -> PeriodPnlSummary: ...
    async def weekly_summary(self, week_start: datetime | None = None) -> WeeklySummary: ...


class TelegramClient(Protocol):
    async def send_message(self, chat_id: str, text: str, parse_mode: str | None = None) -> None: ...


class ConsoleTelegramClient:
    """Dry-run Telegram client used by tests and local simulations."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, chat_id: str, text: str, parse_mode: str | None = None) -> None:
        self.messages.append(text)
        print(text)


class PythonTelegramBotClient:
    """Thin wrapper around python-telegram-bot v20+ Bot."""

    def __init__(self, token: str):
        if Bot is None:
            raise RuntimeError("python-telegram-bot v20+ is required. Install: pip install python-telegram-bot>=20")
        self.bot = Bot(token=token)

    async def send_message(self, chat_id: str, text: str, parse_mode: str | None = "HTML") -> None:
        await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, disable_web_page_preview=True)


def money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def qty(value: float) -> str:
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def format_entry(event: TradeEntryEvent) -> str:
    leverage = f"{event.leverage:g}x" if event.leverage is not None else "N/A"
    unit = f" {event.quantity_unit}" if event.quantity_unit else ""
    oid = f"\n주문ID: <code>{html.escape(event.order_id)}</code>" if event.order_id else ""
    return (
        f"<b>[진입] {html.escape(event.direction.upper())} @ {fmt_price(event.entry_price)} USDT</b>\n"
        f"심볼: {html.escape(event.symbol)}\n"
        f"물량: {qty(event.quantity)}{html.escape(unit)}\n"
        f"레버리지: {html.escape(leverage)}\n"
        f"시간: {ts(event.timestamp)}"
        f"{oid}"
    )


def format_exit(event: TradeExitEvent) -> str:
    pnl_line = f"실현 손익: {money(event.realized_pnl_usdt)} USDT ({pct(event.realized_pnl_pct)})"
    extra = ""
    if event.is_stop_loss:
        extra = f"\n\n⚠️ <b>손절 실행! 손실 금액: {abs(event.realized_pnl_usdt):,.2f} USDT ({pct(event.realized_pnl_pct)})</b>"
    else:
        extra = f"\n수익 금액: {money(max(event.realized_pnl_usdt, 0.0))} USDT"
    oid = f"\n주문ID: <code>{html.escape(event.order_id)}</code>" if event.order_id else ""
    return (
        f"<b>[청산] {html.escape(event.direction.upper())} 종료</b>\n"
        f"심볼: {html.escape(event.symbol)}\n"
        f"진입가: {fmt_price(event.entry_price)} / 청산가: {fmt_price(event.exit_price)}\n"
        f"물량: {qty(event.quantity)}\n"
        f"{pnl_line}\n"
        f"손절 여부: {event.is_stop_loss}"
        f"{extra}\n"
        f"시간: {ts(event.timestamp)}"
        f"{oid}"
    )


def format_positions(positions: Sequence[PositionSummary]) -> str:
    if not positions:
        return "없음"
    lines = []
    for p in positions:
        mark = f" / 현재가 {fmt_price(p.mark_price)}" if p.mark_price is not None else ""
        lines.append(
            f"- {html.escape(p.symbol)} {html.escape(p.direction)} {qty(p.quantity)} @ {fmt_price(p.entry_price)}{mark} | 미실현 {money(p.unrealized_pnl_usdt)} USDT ({pct(p.unrealized_pnl_pct)})"
        )
    return "\n".join(lines)


def format_hourly_report(markets: Sequence[MarketSnapshot], account: AccountSnapshot, hour_pnl: PeriodPnlSummary) -> str:
    market_lines = [
        f"- {html.escape(m.symbol)}: O {fmt_price(m.open)} / H {fmt_price(m.high)} / L {fmt_price(m.low)} / C {fmt_price(m.close)} | {html.escape(m.trend)}"
        for m in markets
    ] or ["- 데이터 없음"]
    return (
        f"<b>[시간 리포트] {ts(datetime.now(timezone.utc))}</b>\n"
        f"시장 1시간봉:\n" + "\n".join(market_lines) + "\n\n"
        f"계좌 평가잔고: {money(account.equity_usdt)} USDT\n"
        f"미실현 손익: {money(account.unrealized_pnl_usdt)} USDT ({pct(account.unrealized_pnl_pct)})\n"
        f"이번 시간 실현 손익: {money(hour_pnl.realized_pnl_usdt)} USDT ({pct(hour_pnl.realized_pnl_pct)})\n"
        f"보유 포지션:\n{format_positions(account.positions)}"
    )


def format_period_report(title: str, summary: PeriodPnlSummary) -> str:
    pf = "N/A" if summary.profit_factor is None else f"{summary.profit_factor:.2f}"
    return (
        f"<b>[{html.escape(title)}]</b>\n"
        f"총 실현 손익: {money(summary.realized_pnl_usdt)} USDT ({pct(summary.realized_pnl_pct)})\n"
        f"최대 손실 폭: {money(summary.max_drawdown_usdt)} USDT ({pct(summary.max_drawdown_pct)})\n"
        f"승률: {summary.win_count}/{summary.trade_count} ({summary.win_rate_pct:.1f}%)\n"
        f"거래 횟수: 총 {summary.trade_count} / 롱 {summary.long_count} / 숏 {summary.short_count}\n"
        f"평균 손익: {money(summary.average_pnl_usdt)} USDT\n"
        f"Profit Factor: {pf}\n"
        f"손절 손실 총합: {money(summary.stop_loss_total_usdt)} USDT"
    )


def format_weekly_report(summary: WeeklySummary) -> str:
    base = format_period_report("주간 리포트", summary)
    start = f"\n시작 잔고: {money(summary.start_equity_usdt)} USDT" if summary.start_equity_usdt else ""
    comment = f"\n다음 주 관심 지표: {html.escape(summary.ai_comment)}" if summary.ai_comment else "\n다음 주 관심 지표: 변동성, 주요 지지/저항, 펀딩비 확인"
    return base + start + comment


class TelegramMonitoringModule:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        provider: ReportDataProvider | None = None,
        client: TelegramClient | None = None,
        rate_limit_per_sec: float = 25.0,
        merge_window_sec: float = 1.0,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")
        self.provider = provider
        self.client = client or PythonTelegramBotClient(self.token or "")
        self.rate_delay = 1.0 / max(1.0, min(rate_limit_per_sec, 30.0))
        self.merge_window_sec = merge_window_sec
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._last_sent_at = 0.0
        self._pending_merge: dict[str, tuple[float, int, str]] = {}

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._send_worker())

    async def stop(self) -> None:
        await self._queue.join()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def send(self, text: str, merge_key: str | None = None) -> None:
        if merge_key:
            now = time.monotonic()
            previous = self._pending_merge.get(merge_key)
            if previous and now - previous[0] <= self.merge_window_sec:
                _, count, first_text = previous
                self._pending_merge[merge_key] = (now, count + 1, first_text)
                return
            if previous and previous[1] > 1:
                await self._queue.put(f"{previous[2]}\n\n동일 타입 알림 {previous[1]}건 병합")
            self._pending_merge[merge_key] = (now, 1, text)
            await asyncio.sleep(self.merge_window_sec)
            current = self._pending_merge.pop(merge_key, None)
            if current:
                suffix = f"\n\n동일 타입 알림 {current[1]}건 병합" if current[1] > 1 else ""
                await self._queue.put(current[2] + suffix)
            return
        await self._queue.put(text)

    async def notify_entry(self, event: TradeEntryEvent) -> None:
        await self.send(format_entry(event), merge_key=f"entry:{event.symbol}:{event.direction}")

    async def notify_exit(self, event: TradeExitEvent) -> None:
        await self.send(format_exit(event), merge_key=f"exit:{event.symbol}:{event.direction}")

    async def notify_error(self, exc: BaseException | str) -> None:
        message = html.escape(str(exc))
        await self.send(f"⚠️ <b>[봇 오류]</b>\n<code>{message}</code>", merge_key="error")

    async def send_status(self) -> None:
        self._require_provider()
        account = await self.provider.account_snapshot()  # type: ignore[union-attr]
        text = (
            f"<b>[상태]</b>\n"
            f"평가잔고: {money(account.equity_usdt)} USDT\n"
            f"미실현 손익: {money(account.unrealized_pnl_usdt)} USDT ({pct(account.unrealized_pnl_pct)})\n"
            f"보유 포지션:\n{format_positions(account.positions)}"
        )
        await self.send(text)

    async def send_hourly_report(self) -> None:
        self._require_provider()
        now = datetime.now(timezone.utc)
        markets = await self.provider.market_snapshots_1h()  # type: ignore[union-attr]
        account = await self.provider.account_snapshot()  # type: ignore[union-attr]
        hour_pnl = await self.provider.realized_pnl_since(now - timedelta(hours=1))  # type: ignore[union-attr]
        await self.send(format_hourly_report(markets, account, hour_pnl))

    async def send_daily_report(self) -> None:
        self._require_provider()
        summary = await self.provider.daily_summary()  # type: ignore[union-attr]
        await self.send(format_period_report("일간 리포트", summary))

    async def send_weekly_report(self) -> None:
        self._require_provider()
        summary = await self.provider.weekly_summary()  # type: ignore[union-attr]
        await self.send(format_weekly_report(summary))

    async def run_schedulers(self, hourly_interval_sec: int = 3600) -> None:
        """Simple asyncio scheduler. Use APScheduler externally if preferred."""
        while True:
            now = datetime.now(timezone.utc)
            if now.minute == 0:
                await self.send_hourly_report()
                if now.hour == 0:
                    await self.send_daily_report()
                    if now.weekday() == 0:
                        await self.send_weekly_report()
            await asyncio.sleep(hourly_interval_sec)

    def build_application(self):
        if Application is None or CommandHandler is None:
            raise RuntimeError("python-telegram-bot v20+ is required for command polling/webhooks")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("daily", self._cmd_daily))
        app.add_handler(CommandHandler("weekly", self._cmd_weekly))
        app.add_handler(CommandHandler("help", self._cmd_help))
        return app

    async def _send_worker(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                elapsed = time.monotonic() - self._last_sent_at
                if elapsed < self.rate_delay:
                    await asyncio.sleep(self.rate_delay - elapsed)
                await self.client.send_message(self.chat_id, text, parse_mode="HTML")
                self._last_sent_at = time.monotonic()
            finally:
                self._queue.task_done()

    async def _cmd_status(self, update, context) -> None:
        await self.send_status()

    async def _cmd_daily(self, update, context) -> None:
        await self.send_daily_report()

    async def _cmd_weekly(self, update, context) -> None:
        await self.send_weekly_report()

    async def _cmd_help(self, update, context) -> None:
        await self.send(
            "<b>[명령어]</b>\n"
            "/status - 현재 계좌 현황\n"
            "/daily - 오늘 손익 요약\n"
            "/weekly - 이번 주 손익 요약\n"
            "/help - 도움말"
        )

    def _require_provider(self) -> None:
        if self.provider is None:
            raise RuntimeError("ReportDataProvider is required for status/daily/weekly/hourly reports")


class DemoReportProvider:
    """Local provider for simulation and integration tests."""

    async def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity_usdt=10_450.25,
            wallet_balance_usdt=10_300.00,
            unrealized_pnl_usdt=150.25,
            unrealized_pnl_pct=1.5,
            positions=(PositionSummary("BTC/USDT", "LONG", 0.05, 65200.0, 66100.0, 45.0, 1.4),),
        )

    async def market_snapshots_1h(self) -> Sequence[MarketSnapshot]:
        return (MarketSnapshot("BTC/USDT", 65000.0, 66200.0, 64800.0, 66100.0, "상승"),)

    async def realized_pnl_since(self, since: datetime) -> PeriodPnlSummary:
        return PeriodPnlSummary(450.0, 4.5, trade_count=1, long_count=1, win_count=1, average_pnl_usdt=450.0, profit_factor=3.2)

    async def daily_summary(self, day: datetime | None = None) -> PeriodPnlSummary:
        return PeriodPnlSummary(820.0, 8.2, trade_count=5, long_count=3, short_count=2, win_count=4, loss_count=1, max_drawdown_usdt=-120.0, max_drawdown_pct=-1.2, stop_loss_total_usdt=-120.0, average_pnl_usdt=164.0, profit_factor=4.8)

    async def weekly_summary(self, week_start: datetime | None = None) -> WeeklySummary:
        return WeeklySummary(2450.0, 24.5, trade_count=23, long_count=13, short_count=10, win_count=16, loss_count=7, max_drawdown_usdt=-350.0, max_drawdown_pct=-3.5, stop_loss_total_usdt=-520.0, average_pnl_usdt=106.52, profit_factor=2.7, start_equity_usdt=10_000.0, ai_comment="BTC 1H 추세와 주요 지지/저항 이탈 여부를 우선 확인")


async def run_demo_simulation() -> list[str]:
    client = ConsoleTelegramClient()
    monitor = TelegramMonitoringModule(chat_id="dry-run", provider=DemoReportProvider(), client=client)
    await monitor.start()
    await monitor.notify_entry(TradeEntryEvent("BTC/USDT", "LONG", 65200.0, 0.05, "BTC", 10, datetime(2026, 6, 1, 14, 23, 5, tzinfo=timezone.utc), "entry-1"))
    await monitor.notify_exit(TradeExitEvent("BTC/USDT", "LONG", 66100.0, 65200.0, 0.05, 450.0, 6.9, False, datetime(2026, 6, 1, 15, 10, 1, tzinfo=timezone.utc), "exit-1"))
    await monitor.notify_exit(TradeExitEvent("ETH/USDT", "SHORT", 2030.0, 2010.0, 0.5, -10.0, -1.0, True, datetime(2026, 6, 1, 15, 30, 1, tzinfo=timezone.utc), "sl-1"))
    await monitor.send_hourly_report()
    await monitor.send_daily_report()
    await monitor.send_weekly_report()
    await monitor.stop()
    return client.messages
