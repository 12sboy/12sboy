from datetime import datetime, timedelta, timezone
import unittest

from trading_bot.bot import BotConfig, TradingBot, completed_candles
from trading_bot.core import Candle, StrategyConfig
from trading_bot.notifier import ConsoleNotifier
from trading_bot.risk import RiskConfig


def _config() -> BotConfig:
    return BotConfig(
        symbols=["ETH/USDT"],
        timeframe="5m",
        mode="paper",
        equity_usdt=1000,
        strategy=StrategyConfig(),
        risk=RiskConfig(),
    )


class ExchangeStateSyncTests(unittest.TestCase):
    def test_sync_exchange_position_restores_short_from_bybit_hedge_leg(self):
        bot = TradingBot(_config(), notifier=ConsoleNotifier())

        class FakeExchange:
            def fetch_positions(self, symbol):
                return [{"positionIdx": 2, "side": "Sell", "size": "0.93", "avgPrice": "2010.55"}]

        bot.exchange = FakeExchange()
        position = bot._sync_exchange_position("ETH/USDT")
        self.assertIsNotNone(position)
        self.assertEqual(position.side, "SHORT")
        self.assertEqual(position.qty, 0.93)
        self.assertEqual(position.entry_price, 2010.55)

    def test_fresh_open_orders_are_not_cancelled_immediately(self):
        notifier = ConsoleNotifier()
        bot = TradingBot(_config(), notifier=notifier)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        class FakeExchange:
            cancelled = False
            def fetch_open_orders(self, symbol):
                return [{"orderId": "abc", "qty": "0.93", "createdTime": str(now_ms)}]
            def cancel_all_orders(self, symbol):
                self.cancelled = True
                return {"list": [{"orderId": "abc"}]}

        exchange = FakeExchange()
        bot.exchange = exchange
        self.assertEqual(bot._cancel_stale_open_orders("ETH/USDT", max_age_sec=600), 0)
        self.assertFalse(exchange.cancelled)
        self.assertEqual(notifier.messages, [])

    def test_cancel_stale_open_orders_calls_cancel_all(self):
        notifier = ConsoleNotifier()
        bot = TradingBot(_config(), notifier=notifier)
        old_ms = int((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000)

        class FakeExchange:
            cancelled = False
            def fetch_open_orders(self, symbol):
                return [{"orderId": "abc", "qty": "0.93", "createdTime": str(old_ms)}]
            def cancel_all_orders(self, symbol):
                self.cancelled = True
                return {"list": [{"orderId": "abc"}]}

        exchange = FakeExchange()
        bot.exchange = exchange
        self.assertEqual(bot._cancel_stale_open_orders("ETH/USDT", max_age_sec=600), 1)
        self.assertTrue(exchange.cancelled)
        self.assertIn("오래된 미체결 주문 1개 취소", notifier.messages[0])

    def test_completed_candles_drops_current_forming_candle(self):
        base = datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc)
        candles = [
            Candle(base - timedelta(minutes=5), 1, 1, 1, 1, 1),
            Candle(base, 2, 2, 2, 2, 1),
        ]
        result = completed_candles(candles, "5m", now=base + timedelta(minutes=2))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].timestamp, base - timedelta(minutes=5))


if __name__ == "__main__":
    unittest.main()
