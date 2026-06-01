from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class MonitorState:
    seen_exec_ids: set[str] = field(default_factory=set)
    seen_closed_order_ids: set[str] = field(default_factory=set)
    order_intents: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "MonitorState":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text())
        except Exception:
            return cls()
        return cls(
            seen_exec_ids=set(raw.get("seen_exec_ids", [])),
            seen_closed_order_ids=set(raw.get("seen_closed_order_ids", [])),
            order_intents=dict(raw.get("order_intents", {})),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "seen_exec_ids": sorted(self.seen_exec_ids)[-1000:],
            "seen_closed_order_ids": sorted(self.seen_closed_order_ids)[-1000:],
            "order_intents": self.order_intents,
        }, ensure_ascii=False, indent=2))


def ms_to_dt(value: str | int | float | None) -> datetime:
    try:
        raw = int(float(value or 0))
    except (TypeError, ValueError):
        raw = 0
    if raw <= 0:
        return datetime.now(timezone.utc)
    if raw < 10_000_000_000:
        raw *= 1000
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


def fmt_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def fmt_qty(value: float | str | None) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.8f}".rstrip("0").rstrip(".") or "0"


def fmt_price(value: float | str | None) -> str:
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def direction_from_entry_side(side: str) -> str:
    return "LONG" if side.lower() == "buy" else "SHORT"


def closed_direction(close_side: str) -> str:
    # Bybit closed-pnl side is the close order side. Buy closes SHORT, Sell closes LONG.
    return "SHORT" if close_side.lower() == "buy" else "LONG"


def pnl_pct(pnl: float, entry_value: float) -> float:
    if entry_value <= 0:
        return 0.0
    return pnl / entry_value * 100


def is_stop_loss_reason(reason: str | None) -> bool:
    text = (reason or "").lower()
    return "stop" in text or "손절" in text


def format_entry_fill(exec_: dict, intent: dict | None = None, leverage: float | None = None) -> str:
    side = (intent or {}).get("direction") or direction_from_entry_side(str(exec_.get("side") or ""))
    lev = (intent or {}).get("leverage") or leverage
    lev_text = f"{float(lev):g}x" if lev else "N/A"
    ts = ms_to_dt(exec_.get("execTime")).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"[진입 체결] {side} @ {fmt_price(exec_.get('execPrice'))} USDT\n"
        f"심볼: {exec_.get('symbol')}\n"
        f"물량: {fmt_qty(exec_.get('execQty'))}\n"
        f"레버리지: {lev_text}\n"
        f"수수료: {fmt_money(float(exec_.get('execFee') or 0))} USDT\n"
        f"시간: {ts}\n"
        f"주문ID: {exec_.get('orderId')}"
    )


def format_close_fill(closed: dict, intent: dict | None = None) -> str:
    pnl = float(closed.get("closedPnl") or 0)
    entry_value = float(closed.get("cumEntryValue") or 0)
    pct = pnl_pct(pnl, entry_value)
    direction = (intent or {}).get("direction") or closed_direction(str(closed.get("side") or ""))
    stop = bool((intent or {}).get("stop_loss")) or is_stop_loss_reason((intent or {}).get("reason"))
    ts = ms_to_dt(closed.get("updatedTime") or closed.get("createdTime")).strftime("%Y-%m-%d %H:%M:%S UTC")
    extra = f"\n⚠️ 손절 실행! 손실 금액: {abs(pnl):,.2f} USDT ({fmt_pct(pct)})" if stop or pnl < 0 else f"\n수익 금액: {max(pnl, 0):,.2f} USDT"
    return (
        f"[청산 체결] {direction} 종료\n"
        f"심볼: {closed.get('symbol')}\n"
        f"진입가: {fmt_price(closed.get('avgEntryPrice'))} / 청산가: {fmt_price(closed.get('avgExitPrice'))}\n"
        f"물량: {fmt_qty(closed.get('qty') or closed.get('closedSize'))}\n"
        f"실현 손익: {fmt_money(pnl)} USDT ({fmt_pct(pct)})\n"
        f"손절 여부: {bool(stop or pnl < 0)}"
        f"{extra}\n"
        f"시간: {ts}\n"
        f"주문ID: {closed.get('orderId')}"
    )


@dataclass(frozen=True)
class PnlStats:
    realized_pnl: float = 0.0
    realized_pct: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    long_trades: int = 0
    short_trades: int = 0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    stop_loss_total: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float | None = None


def compute_pnl_stats(closed_rows: Iterable[dict], since: datetime | None = None, starting_equity: float | None = None) -> PnlStats:
    rows = []
    since_ms = int(since.timestamp() * 1000) if since else None
    for row in closed_rows:
        ts_ms = int(float(row.get("updatedTime") or row.get("createdTime") or 0))
        if since_ms is not None and ts_ms < since_ms:
            continue
        rows.append(row)
    rows.sort(key=lambda r: int(float(r.get("updatedTime") or r.get("createdTime") or 0)))
    pnls = [float(r.get("closedPnl") or 0) for r in rows]
    total = sum(pnls)
    entry_value = sum(float(r.get("cumEntryValue") or 0) for r in rows)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    equity = starting_equity or entry_value or 0
    cumulative = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    long_trades = sum(1 for r in rows if closed_direction(str(r.get("side") or "")) == "LONG")
    short_trades = sum(1 for r in rows if closed_direction(str(r.get("side") or "")) == "SHORT")
    stop_loss_total = sum(p for r, p in zip(rows, pnls) if p < 0)
    return PnlStats(
        realized_pnl=total,
        realized_pct=(total / equity * 100) if equity else 0.0,
        trades=len(rows),
        wins=wins,
        losses=losses,
        long_trades=long_trades,
        short_trades=short_trades,
        max_drawdown=max_dd,
        max_drawdown_pct=(max_dd / equity * 100) if equity else 0.0,
        stop_loss_total=stop_loss_total,
        avg_pnl=(total / len(rows)) if rows else 0.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss else (None if gross_profit == 0 else float("inf")),
    )


def format_period_stats(title: str, stats: PnlStats) -> str:
    pf = "N/A" if stats.profit_factor is None else ("∞" if stats.profit_factor == float("inf") else f"{stats.profit_factor:.2f}")
    win_rate = stats.wins / stats.trades * 100 if stats.trades else 0.0
    return (
        f"[{title}]\n"
        f"총 실현 손익: {fmt_money(stats.realized_pnl)} USDT ({fmt_pct(stats.realized_pct)})\n"
        f"최대 손실 폭: {fmt_money(stats.max_drawdown)} USDT ({fmt_pct(stats.max_drawdown_pct)})\n"
        f"승률: {stats.wins}/{stats.trades} ({win_rate:.1f}%)\n"
        f"거래 횟수: 총 {stats.trades} / 롱 {stats.long_trades} / 숏 {stats.short_trades}\n"
        f"평균 손익: {fmt_money(stats.avg_pnl)} USDT\n"
        f"Profit Factor: {pf}\n"
        f"손절/손실 총합: {fmt_money(stats.stop_loss_total)} USDT"
    )
