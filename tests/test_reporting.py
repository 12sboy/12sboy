import unittest
from datetime import datetime, timezone

from trading_bot.core import Candle
from trading_bot.reporting import classify_market, format_hourly_report


def c(ts, close):
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc), close, close + 1, close - 1, close, 100)


class ReportingTests(unittest.TestCase):
    def test_classify_market_up_down_sideways(self):
        self.assertEqual(classify_market([c("2026-01-01T00:00:00", 100), c("2026-01-01T01:00:00", 103)]), "상승")
        self.assertEqual(classify_market([c("2026-01-01T00:00:00", 100), c("2026-01-01T01:00:00", 97)]), "하락")
        self.assertEqual(classify_market([c("2026-01-01T00:00:00", 100), c("2026-01-01T01:00:00", 100.2)]), "횡보")

    def test_format_hourly_report_contains_market_balance_and_positions(self):
        snapshots = {
            "BTC/USDT": {"market": "상승", "last": 103.0, "change_pct": 3.0, "vwap": 101.0, "ema8": 102.0},
            "ETH/USDT": {"market": "횡보", "last": 100.2, "change_pct": 0.2, "vwap": 100.0, "ema8": 100.1},
        }
        text = format_hourly_report(snapshots, {"USDT": {"equity": 123.45}}, {"BTC/USDT": "LONG"})
        self.assertIn("1시간 시장 리포트", text)
        self.assertIn("BTC/USDT: 상승", text)
        self.assertIn("USDT equity: 123.45", text)
        self.assertIn("BTC/USDT=LONG", text)


if __name__ == "__main__":
    unittest.main()
