import unittest

from trading_bot.help import TELEGRAM_BOT_COMMANDS, command_help, telegram_help


class HelpTests(unittest.TestCase):
    def test_command_help_lists_core_commands(self):
        text = command_help()
        self.assertIn("python3 -m trading_bot help", text)
        self.assertIn("python3 -m trading_bot report", text)
        self.assertIn("python3 -m trading_bot once", text)
        self.assertIn("python3 -m trading_bot telegram-poll", text)
        self.assertIn("python3 -m trading_bot run", text)
        self.assertIn("VWAP 이탈 즉시 손절", text)

    def test_telegram_help_lists_safe_commands(self):
        text = telegram_help()
        self.assertIn("/help", text)
        self.assertIn("/status", text)
        self.assertIn("/daily", text)
        self.assertIn("/weekly", text)
        self.assertIn("주문 실행 명령을 받지 않습니다", text)
        self.assertEqual({c["command"] for c in TELEGRAM_BOT_COMMANDS}, {"help", "status", "daily", "weekly"})


if __name__ == "__main__":
    unittest.main()
