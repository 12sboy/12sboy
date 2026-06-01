import json
import unittest
from unittest.mock import patch

from trading_bot.notifier import ConsoleNotifier, TelegramNotifier
from trading_bot.bot import BotConfig, TradingBot
from trading_bot.core import StrategyConfig
from trading_bot.risk import RiskConfig


class NotifierTests(unittest.TestCase):
    def test_console_notifier_records_messages(self):
        notifier = ConsoleNotifier()
        notifier.send("hello")
        self.assertEqual(notifier.messages, ["hello"])

    def test_telegram_notifier_calls_bot_api(self):
        captured = {}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"ok":true}'

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode())
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("urllib.request.urlopen", fake_urlopen):
            TelegramNotifier("token", "chat").send("체결")
        self.assertIn("/bottoken/sendMessage", captured["url"])
        self.assertEqual(captured["data"]["chat_id"], "chat")
        self.assertEqual(captured["data"]["text"], "체결")

    def test_telegram_notifier_get_updates_and_set_commands(self):
        calls = []

        class FakeResponse:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body

        def fake_urlopen(req, timeout):
            calls.append((req.full_url, json.loads(req.data.decode()), timeout))
            if req.full_url.endswith("/getUpdates"):
                return FakeResponse(b'{"ok":true,"result":[{"update_id":1}]}')
            return FakeResponse(b'{"ok":true,"result":true}')

        notifier = TelegramNotifier("token", "chat")
        with patch("urllib.request.urlopen", fake_urlopen):
            updates = notifier.get_updates(offset=10)
            notifier.set_my_commands([{"command": "help", "description": "h"}])
        self.assertEqual(updates, [{"update_id": 1}])
        self.assertIn("/bottoken/getUpdates", calls[0][0])
        self.assertEqual(calls[0][1]["offset"], 10)
        self.assertIn("/bottoken/setMyCommands", calls[1][0])

    def test_trading_bot_processes_telegram_help_command(self):
        class FakeTelegram(TelegramNotifier):
            def __init__(self):
                super().__init__("token", "123")
                object.__setattr__(self, "sent", [])
            def get_updates(self, offset=None, timeout_sec=0):
                return [{"update_id": 7, "message": {"chat": {"id": 123}, "text": "/help"}}]
            def send(self, text):
                self.sent.append(text)

        config = BotConfig(
            symbols=["BTC/USDT"],
            timeframe="5m",
            mode="paper",
            equity_usdt=1000,
            strategy=StrategyConfig(),
            risk=RiskConfig(),
            telegram_token="token",
            telegram_chat_id="123",
        )
        notifier = FakeTelegram()
        bot = TradingBot(config, notifier=notifier)
        handled = bot.process_telegram_commands_once()
        self.assertEqual(handled, 1)
        self.assertEqual(bot._telegram_update_offset, 8)
        self.assertIn("매매봇 도움말", notifier.sent[0])


if __name__ == "__main__":
    unittest.main()
