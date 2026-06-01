import unittest
from datetime import datetime, timezone

from trading_bot.core import Candle
from trading_bot.reporting import classify_market, format_hourly_report, format_trade_message


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

    def test_trade_message_distinguishes_entry_order_from_fill(self):
        text = format_trade_message({
            "symbol": "ETH/USDT",
            "signal": {"action": "SELL", "side": "SHORT", "reason": "breakdown", "price": 2009.8},
            "order": {"orderId": "abc", "qty": "0.45", "price": "2009.87", "orderType": "Limit", "reduceOnly": False},
        })
        self.assertIn("진입 지정가 주문 접수", text)
        self.assertIn("아직 미체결일 수 있음", text)
        self.assertIn("수량: 0.45", text)

    def test_trade_message_marks_market_close_as_close_order(self):
        text = format_trade_message({
            "symbol": "BTC/USDT",
            "signal": {"action": "CLOSE", "side": "SHORT", "reason": "fixed_take_profit", "price": 73542.6},
            "order": {"orderId": "def", "qty": "0.025", "price": "73542.6", "orderType": "Market", "reduceOnly": True},
        })
        self.assertIn("청산 시장가 주문 전송", text)
        self.assertIn("Bybit execution", text)


if __name__ == "__main__":
    unittest.main()
