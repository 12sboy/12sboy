import unittest
from datetime import datetime, timezone

from trading_bot.core import Candle
from trading_bot.backtest import BacktestEngine


def c(ts, o, h, l, close, volume=100.0):
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc), o, h, l, close, volume)


class BacktestTests(unittest.TestCase):
    def test_backtest_opens_and_closes_trade_from_strategy_signals(self):
        candles = [
            c("2026-01-01T00:00:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:05:00", 100, 101, 99, 100, 100),
            c("2026-01-01T00:10:00", 100, 105, 100, 104, 200),
            c("2026-01-01T00:15:00", 104, 105, 100.1, 101, 100),
            c("2026-01-01T00:20:00", 101, 112, 101, 111, 100),
            c("2026-01-01T00:25:00", 111, 112, 106, 107, 100),
        ]
        engine = BacktestEngine(initial_equity=1000)
        report = engine.run(candles, symbol="BTC/USDT", levels={"pre_high": 103, "pre_low": 95, "prior_high": 110, "prior_low": 90})
        self.assertGreaterEqual(report["trades"], 1)
        self.assertIn("equity", report)
        self.assertIn("orders", report)


if __name__ == "__main__":
    unittest.main()
