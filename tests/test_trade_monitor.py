from datetime import datetime, timezone
import unittest

from trading_bot.trade_monitor import (
    compute_pnl_stats,
    format_close_fill,
    format_entry_fill,
    format_period_stats,
)


class TradeMonitorTests(unittest.TestCase):
    def test_entry_fill_message_shows_direction_price_qty_leverage(self):
        msg = format_entry_fill({
            "symbol": "BTCUSDT",
            "side": "Buy",
            "execPrice": "65200",
            "execQty": "0.05",
            "execFee": "0.5",
            "execTime": "1780281804620",
            "orderId": "entry-1",
        }, {"direction": "LONG", "leverage": 10})
        self.assertIn("[진입 체결] LONG @ 65,200.00 USDT", msg)
        self.assertIn("물량: 0.05", msg)
        self.assertIn("레버리지: 10x", msg)

    def test_close_fill_message_shows_pnl_and_stop_loss_warning(self):
        msg = format_close_fill({
            "symbol": "ETHUSDT",
            "side": "Buy",
            "orderId": "close-1",
            "closedPnl": "-12.5",
            "cumEntryValue": "1000",
            "avgEntryPrice": "2000",
            "avgExitPrice": "2025",
            "qty": "0.5",
            "updatedTime": "1780281804620",
        }, {"direction": "SHORT", "reason": "vwap_stop_loss"})
        self.assertIn("[청산 체결] SHORT 종료", msg)
        self.assertIn("실현 손익: -12.50 USDT (-1.2%)", msg)
        self.assertIn("⚠️ 손절 실행", msg)
        self.assertIn("손실 금액: 12.50 USDT", msg)

    def test_period_stats_daily_weekly(self):
        rows = [
            {"updatedTime": "1780281804620", "closedPnl": "100", "cumEntryValue": "1000", "side": "Sell"},
            {"updatedTime": "1780281904620", "closedPnl": "-50", "cumEntryValue": "1000", "side": "Buy"},
        ]
        stats = compute_pnl_stats(rows, since=datetime(2026, 1, 1, tzinfo=timezone.utc), starting_equity=1000)
        self.assertEqual(stats.trades, 2)
        self.assertEqual(stats.wins, 1)
        self.assertEqual(stats.losses, 1)
        self.assertAlmostEqual(stats.realized_pnl, 50)
        self.assertAlmostEqual(stats.realized_pct, 5.0)
        text = format_period_stats("일간 손익 리포트", stats)
        self.assertIn("총 실현 손익: +50.00 USDT (+5.0%)", text)
        self.assertIn("승률: 1/2 (50.0%)", text)


if __name__ == "__main__":
    unittest.main()
