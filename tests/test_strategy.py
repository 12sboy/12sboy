import unittest
from datetime import datetime, timezone

from trading_bot.core import Candle, PBInvestingStrategy, StrategyConfig, Position


def c(ts, o, h, l, close, volume=100.0):
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc), o, h, l, close, volume)


class PBInvestingStrategyTests(unittest.TestCase):
    def test_indicators_compute_daily_vwap_and_ema8(self):
        candles = [
            c("2026-01-01T00:00:00", 10, 12, 9, 11, 10),
            c("2026-01-01T00:05:00", 11, 13, 10, 12, 20),
            c("2026-01-02T00:00:00", 20, 22, 18, 21, 5),
        ]
        strategy = PBInvestingStrategy(StrategyConfig())
        enriched = strategy.compute_indicators(candles)
        expected_day1_vwap_2 = (((12 + 9 + 11) / 3) * 10 + ((13 + 10 + 12) / 3) * 20) / 30
        self.assertAlmostEqual(enriched[1].vwap, expected_day1_vwap_2, places=8)
        self.assertAlmostEqual(enriched[2].vwap, (22 + 18 + 21) / 3, places=8)
        self.assertIsNotNone(enriched[-1].ema8)

    def test_long_entry_after_breakout_then_vwap_retest(self):
        strategy = PBInvestingStrategy(StrategyConfig(retest_tolerance_pct=0.002, ema_space_take_profit_pct=0.03, require_candle_direction=False))
        candles = [
            c("2026-01-01T00:00:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:05:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:10:00", 100, 105, 100, 104, 200),  # breakout above pre high 103
            c("2026-01-01T00:15:00", 104, 105, 100.2, 102, 100),  # retest near VWAP while close above VWAP
        ]
        signal = strategy.on_candle(candles, symbol="BTC/USDT", levels={"pre_high": 103, "pre_low": 95, "prior_high": 110, "prior_low": 101.7})
        self.assertEqual(signal.action, "BUY")
        self.assertEqual(signal.side, "LONG")
        self.assertTrue(signal.a_plus)
        self.assertGreater(signal.position_size_multiplier, 1.0)

    def test_never_long_when_close_below_vwap(self):
        strategy = PBInvestingStrategy(StrategyConfig(retest_tolerance_pct=0.01, require_candle_direction=False))
        candles = [
            c("2026-01-01T00:00:00", 100, 110, 90, 100, 1000),
            c("2026-01-01T00:05:00", 100, 106, 100, 104, 10),
            c("2026-01-01T00:10:00", 104, 105, 95, 96, 10),
        ]
        signal = strategy.on_candle(candles, "BTC/USDT", {"pre_high": 103, "pre_low": 95, "prior_high": 110, "prior_low": 90})
        self.assertEqual(signal.action, "HOLD")

    def test_short_entry_after_breakdown_then_vwap_retest(self):
        strategy = PBInvestingStrategy(StrategyConfig(retest_tolerance_pct=0.005, require_candle_direction=False))
        candles = [
            c("2026-01-01T00:00:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:05:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:10:00", 100, 100, 94, 96, 200),   # breakdown below pre low 97
            c("2026-01-01T00:15:00", 96, 99.8, 95, 98, 100),   # retest near VWAP while below VWAP
        ]
        signal = strategy.on_candle(candles, "ETH/USDT", {"pre_high": 105, "pre_low": 97, "prior_high": 110, "prior_low": 90})
        self.assertEqual(signal.action, "SELL")
        self.assertEqual(signal.side, "SHORT")

    def test_long_exit_take_profit_then_ema_trailing_stop_and_vwap_stop_loss(self):
        strategy = PBInvestingStrategy(StrategyConfig(ema_space_take_profit_pct=0.02, fixed_take_profit_pct=None))
        position = Position(symbol="BTC/USDT", side="LONG", qty=1.0, entry_price=100.0)
        take_profit = strategy.manage_position(
            position,
            c("2026-01-01T00:20:00", 104, 108, 103, 108, 100),
            ema8=104.0,
            vwap=100.0,
        )
        self.assertEqual(take_profit.action, "TAKE_PROFIT")
        self.assertAlmostEqual(take_profit.qty_fraction, 0.5)
        position.scaled_out = True
        trailing = strategy.manage_position(
            position,
            c("2026-01-01T00:25:00", 104, 105, 101, 102, 100),
            ema8=103.0,
            vwap=100.0,
        )
        self.assertEqual(trailing.action, "CLOSE")
        self.assertEqual(trailing.reason, "ema8_trailing_stop")
        stop = strategy.manage_position(
            Position(symbol="BTC/USDT", side="LONG", qty=1, entry_price=100),
            c("2026-01-01T00:25:00", 99, 100, 95, 98, 100),
            ema8=101.0,
            vwap=99.0,
        )
        self.assertEqual(stop.action, "CLOSE")
        self.assertEqual(stop.reason, "vwap_stop_loss")

    def test_require_candle_direction_blocks_weak_retest_candles(self):
        strategy = PBInvestingStrategy(StrategyConfig(retest_tolerance_pct=0.02, require_candle_direction=True))
        candles = [
            c("2026-01-01T00:00:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:05:00", 100, 106, 100, 105, 200),
            c("2026-01-01T00:10:00", 105, 106, 100, 102, 100),
        ]
        signal = strategy.on_candle(candles, "BTC/USDT", {"pre_high": 103, "pre_low": 95, "prior_high": 110, "prior_low": 100})
        self.assertEqual(signal.action, "HOLD")

    def test_fixed_take_profit_closes_full_position_before_ema_scaleout(self):
        strategy = PBInvestingStrategy(StrategyConfig(fixed_take_profit_pct=0.0025))
        signal = strategy.manage_position(
            Position(symbol="BTC/USDT", side="LONG", qty=1.0, entry_price=100.0),
            c("2026-01-01T00:20:00", 100, 101, 100, 100.3, 100),
            ema8=99.0,
            vwap=100.0,
        )
        self.assertEqual(signal.action, "CLOSE")
        self.assertEqual(signal.reason, "fixed_take_profit")
        self.assertAlmostEqual(signal.qty_fraction, 1.0)


if __name__ == "__main__":
    unittest.main()
