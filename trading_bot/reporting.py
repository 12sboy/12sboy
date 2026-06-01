from __future__ import annotations

from datetime import datetime, timezone

from .core import Candle


def classify_market(candles: list[Candle], threshold_pct: float = 0.5) -> str:
    if len(candles) < 2:
        return "데이터부족"
    first = candles[0].close
    last = candles[-1].close
    if first <= 0:
        return "데이터오류"
    change_pct = (last - first) / first * 100
    if change_pct >= threshold_pct:
        return "상승"
    if change_pct <= -threshold_pct:
        return "하락"
    return "횡보"


def market_snapshot(candles: list[Candle], vwap: float | None = None, ema8: float | None = None) -> dict:
    first = candles[0].close if candles else 0
    last = candles[-1].close if candles else 0
    change_pct = ((last - first) / first * 100) if first else 0.0
    return {
        "market": classify_market(candles),
        "last": round(last, 6),
        "change_pct": round(change_pct, 4),
        "vwap": round(vwap, 6) if vwap is not None else None,
        "ema8": round(ema8, 6) if ema8 is not None else None,
    }


def format_trade_message(event: dict) -> str:
    signal = event.get("signal", {})
    order = event.get("order", {})
    return (
        f"체결/주문 알림\n"
        f"심볼: {event.get('symbol')}\n"
        f"액션: {signal.get('action')} {signal.get('side') or ''}\n"
        f"사유: {signal.get('reason')}\n"
        f"가격: {signal.get('price')}\n"
        f"수량: {order.get('qty')}\n"
        f"주문ID: {order.get('orderId') or order.get('id')}"
    )


def format_hourly_report(snapshots: dict[str, dict], balance: dict, positions: dict[str, object]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"1시간 시장 리포트 ({now})"]
    for symbol, snap in snapshots.items():
        lines.append(
            f"{symbol}: {snap.get('market')} | 현재가 {snap.get('last')} | 1h {snap.get('change_pct')}% | VWAP {snap.get('vwap')} | EMA8 {snap.get('ema8')}"
        )
    usdt = balance.get("USDT", {}) if balance else {}
    if usdt:
        lines.append(f"USDT equity: {usdt.get('equity')} | wallet: {usdt.get('walletBalance')}")
    if positions:
        parts = []
        for symbol, position in positions.items():
            side = position if isinstance(position, str) else getattr(position, "side", str(position))
            parts.append(f"{symbol}={side}")
        lines.append("포지션: " + ", ".join(parts))
    else:
        lines.append("포지션: 없음")
    return "\n".join(lines)
