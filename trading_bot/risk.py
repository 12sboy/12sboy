from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.005
    max_notional_pct: float = 0.25
    min_qty: float = 0.0
    leverage: float = 5.0
    max_order_notional_usdt: float | None = None


def calculate_order_qty(equity_usdt: float, entry_price: float, stop_price: float, config: RiskConfig) -> float:
    """Position quantity using fixed risk and a notional cap.

    qty = min(risk dollars / stop distance, max notional / entry price)
    """
    if equity_usdt <= 0 or entry_price <= 0 or stop_price <= 0:
        return 0.0
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0.0
    risk_dollars = equity_usdt * config.risk_per_trade_pct
    risk_qty = risk_dollars / stop_distance
    max_notional = equity_usdt * config.max_notional_pct * max(config.leverage, 1.0)
    if config.max_order_notional_usdt is not None and config.max_order_notional_usdt > 0:
        max_notional = min(max_notional, config.max_order_notional_usdt)
    cap_qty = max_notional / entry_price
    qty = min(risk_qty, cap_qty)
    return qty if qty >= config.min_qty else 0.0
