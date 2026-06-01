from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

Side = Literal["LONG", "SHORT"]
Action = Literal["HOLD", "BUY", "SELL", "TAKE_PROFIT", "CLOSE"]


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0


@dataclass(frozen=True)
class EnrichedCandle(Candle):
    ema8: float | None = None
    vwap: float | None = None


@dataclass
class Position:
    symbol: str
    side: Side
    qty: float
    entry_price: float
    scaled_out: bool = False


@dataclass(frozen=True)
class StrategyConfig:
    ema_length: int = 8
    retest_tolerance_pct: float = 0.002
    a_plus_alignment_pct: float = 0.003
    a_plus_size_multiplier: float = 1.5
    normal_size_multiplier: float = 1.0
    ema_space_take_profit_pct: float = 0.025
    take_profit_fraction: float = 0.5
    fixed_take_profit_pct: float | None = 0.0025
    require_candle_direction: bool = True


@dataclass(frozen=True)
class Signal:
    action: Action
    symbol: str
    side: Side | None = None
    reason: str = ""
    price: float | None = None
    qty_fraction: float = 1.0
    a_plus: bool = False
    position_size_multiplier: float = 1.0
    meta: dict[str, Any] | None = None


class PBInvestingStrategy:
    """Simple VWAP/EMA8 breakout-retest strategy adapted to 24/7 crypto.

    Level names accepted in ``levels``:
    - pre_high / pre_low: session high/low used as breakout filter.
      For crypto these can be configured as Asia/London/NY session levels or
      any user-defined range.
    - prior_high / prior_low: previous daily levels.
    """

    def __init__(self, config: StrategyConfig | None = None):
        self.config = config or StrategyConfig()
        self._long_breakout_seen: set[str] = set()
        self._short_breakdown_seen: set[str] = set()

    def compute_indicators(self, candles: list[Candle]) -> list[EnrichedCandle]:
        if not candles:
            return []
        out: list[EnrichedCandle] = []
        alpha = 2.0 / (self.config.ema_length + 1)
        ema: float | None = None
        current_day = None
        cumulative_pv = 0.0
        cumulative_vol = 0.0
        for candle in candles:
            day = candle.timestamp.date()
            if day != current_day:
                current_day = day
                cumulative_pv = 0.0
                cumulative_vol = 0.0
            if ema is None:
                ema = candle.close
            else:
                ema = alpha * candle.close + (1 - alpha) * ema
            cumulative_pv += candle.typical_price * candle.volume
            cumulative_vol += candle.volume
            vwap = cumulative_pv / cumulative_vol if cumulative_vol else candle.typical_price
            out.append(EnrichedCandle(**candle.__dict__, ema8=ema, vwap=vwap))
        return out

    def on_candle(self, candles: list[Candle], symbol: str, levels: dict[str, float]) -> Signal:
        enriched = self.compute_indicators(candles)
        if len(enriched) < 2:
            return Signal("HOLD", symbol, reason="not_enough_data")
        prev = enriched[-2]
        cur = enriched[-1]
        pre_high = levels.get("pre_high")
        pre_low = levels.get("pre_low")
        if pre_high is None or pre_low is None or cur.vwap is None:
            return Signal("HOLD", symbol, reason="missing_levels")

        # ``on_candle`` may be called statelessly with the full candle window
        # (backtests/tests) or incrementally live. Reconstruct earlier breakout
        # state from the supplied window, then include the newest transition.
        long_setup_seen = symbol in self._long_breakout_seen
        short_setup_seen = symbol in self._short_breakdown_seen
        for older, newer in zip(enriched[:-2], enriched[1:-1]):
            if older.close <= pre_high < newer.close:
                long_setup_seen = True
            if older.close >= pre_low > newer.close:
                short_setup_seen = True
        if prev.close <= pre_high < cur.close:
            long_setup_seen = True
            self._long_breakout_seen.add(symbol)
        if prev.close >= pre_low > cur.close:
            short_setup_seen = True
            self._short_breakdown_seen.add(symbol)

        touches_vwap = cur.low <= cur.vwap * (1 + self.config.retest_tolerance_pct) and cur.high >= cur.vwap * (1 - self.config.retest_tolerance_pct)

        long_candle_ok = not self.config.require_candle_direction or cur.close > cur.open
        short_candle_ok = not self.config.require_candle_direction or cur.close < cur.open

        if long_setup_seen and cur.close >= cur.vwap and touches_vwap and long_candle_ok:
            a_plus = self._is_a_plus(cur.vwap, levels)
            self._long_breakout_seen.discard(symbol)
            return Signal(
                "BUY",
                symbol,
                side="LONG",
                reason="breakout_then_vwap_retest",
                price=cur.vwap,
                a_plus=a_plus,
                position_size_multiplier=self.config.a_plus_size_multiplier if a_plus else self.config.normal_size_multiplier,
                meta={"vwap": cur.vwap, "ema8": cur.ema8, "levels": levels},
            )

        if short_setup_seen and cur.close <= cur.vwap and touches_vwap and short_candle_ok:
            a_plus = self._is_a_plus(cur.vwap, levels)
            self._short_breakdown_seen.discard(symbol)
            return Signal(
                "SELL",
                symbol,
                side="SHORT",
                reason="breakdown_then_vwap_retest",
                price=cur.vwap,
                a_plus=a_plus,
                position_size_multiplier=self.config.a_plus_size_multiplier if a_plus else self.config.normal_size_multiplier,
                meta={"vwap": cur.vwap, "ema8": cur.ema8, "levels": levels},
            )

        return Signal("HOLD", symbol, reason="no_setup", meta={"vwap": cur.vwap, "ema8": cur.ema8})

    def manage_position(self, position: Position, candle: Candle, ema8: float, vwap: float) -> Signal:
        symbol = position.symbol
        if position.side == "LONG":
            if candle.close < vwap:
                return Signal("CLOSE", symbol, side="LONG", reason="vwap_stop_loss", price=candle.close)
            if self.config.fixed_take_profit_pct is not None and candle.close >= position.entry_price * (1 + self.config.fixed_take_profit_pct):
                return Signal("CLOSE", symbol, side="LONG", reason="fixed_take_profit", price=candle.close)
            if not position.scaled_out and candle.close >= ema8 * (1 + self.config.ema_space_take_profit_pct):
                return Signal("TAKE_PROFIT", symbol, side="LONG", reason="ema8_space_take_profit", price=candle.close, qty_fraction=self.config.take_profit_fraction)
            if position.scaled_out and candle.close < ema8:
                return Signal("CLOSE", symbol, side="LONG", reason="ema8_trailing_stop", price=candle.close)
        else:
            if candle.close > vwap:
                return Signal("CLOSE", symbol, side="SHORT", reason="vwap_stop_loss", price=candle.close)
            if self.config.fixed_take_profit_pct is not None and candle.close <= position.entry_price * (1 - self.config.fixed_take_profit_pct):
                return Signal("CLOSE", symbol, side="SHORT", reason="fixed_take_profit", price=candle.close)
            if not position.scaled_out and candle.close <= ema8 * (1 - self.config.ema_space_take_profit_pct):
                return Signal("TAKE_PROFIT", symbol, side="SHORT", reason="ema8_space_take_profit", price=candle.close, qty_fraction=self.config.take_profit_fraction)
            if position.scaled_out and candle.close > ema8:
                return Signal("CLOSE", symbol, side="SHORT", reason="ema8_trailing_stop", price=candle.close)
        return Signal("HOLD", symbol, side=position.side, reason="hold_position")

    def _is_a_plus(self, vwap: float, levels: dict[str, float]) -> bool:
        for key in ("pre_high", "pre_low", "prior_high", "prior_low"):
            value = levels.get(key)
            if value and abs(vwap - value) / value <= self.config.a_plus_alignment_pct:
                return True
        return False
