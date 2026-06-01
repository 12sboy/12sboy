from __future__ import annotations

from dataclasses import asdict

from .core import Candle, PBInvestingStrategy, Position, StrategyConfig
from .risk import RiskConfig, calculate_order_qty


class BacktestEngine:
    def __init__(self, initial_equity: float = 1000.0, strategy_config: StrategyConfig | None = None, risk_config: RiskConfig | None = None):
        self.initial_equity = initial_equity
        self.strategy = PBInvestingStrategy(strategy_config)
        self.risk_config = risk_config or RiskConfig()

    def run(self, candles: list[Candle], symbol: str, levels: dict[str, float]) -> dict:
        equity = self.initial_equity
        position: Position | None = None
        orders: list[dict] = []
        closed_trades = 0
        enriched = self.strategy.compute_indicators(candles)

        for i, candle in enumerate(candles):
            ind = enriched[i]
            if position:
                sig = self.strategy.manage_position(position, candle, ema8=ind.ema8 or candle.close, vwap=ind.vwap or candle.close)
                if sig.action == "TAKE_PROFIT":
                    close_qty = position.qty * sig.qty_fraction
                    pnl = _pnl(position.side, position.entry_price, sig.price or candle.close, close_qty)
                    equity += pnl
                    position.qty -= close_qty
                    position.scaled_out = True
                    orders.append({"action": sig.action, "side": position.side, "qty": close_qty, "price": sig.price, "pnl": pnl, "reason": sig.reason})
                elif sig.action == "CLOSE":
                    pnl = _pnl(position.side, position.entry_price, sig.price or candle.close, position.qty)
                    equity += pnl
                    orders.append({"action": sig.action, "side": position.side, "qty": position.qty, "price": sig.price, "pnl": pnl, "reason": sig.reason})
                    position = None
                    closed_trades += 1
                continue

            signal = self.strategy.on_candle(candles[: i + 1], symbol, levels)
            if signal.action in ("BUY", "SELL") and signal.side:
                entry = signal.price or candle.close
                stop = ind.vwap if ind.vwap else entry
                if signal.side == "LONG":
                    stop = min(stop, candle.low)
                else:
                    stop = max(stop, candle.high)
                qty = calculate_order_qty(equity, entry, stop, self.risk_config) * signal.position_size_multiplier
                if qty > 0:
                    position = Position(symbol=symbol, side=signal.side, qty=qty, entry_price=entry)
                    orders.append({"action": signal.action, "side": signal.side, "qty": qty, "price": entry, "reason": signal.reason, "a_plus": signal.a_plus})

        if position:
            last = candles[-1]
            pnl = _pnl(position.side, position.entry_price, last.close, position.qty)
            equity += pnl
            closed_trades += 1
            orders.append({"action": "CLOSE", "side": position.side, "qty": position.qty, "price": last.close, "pnl": pnl, "reason": "end_of_backtest"})

        return {"symbol": symbol, "initial_equity": self.initial_equity, "equity": round(equity, 6), "pnl": round(equity - self.initial_equity, 6), "trades": closed_trades, "orders": orders, "config": {"risk": asdict(self.risk_config)}}


def _pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    if side == "LONG":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty
