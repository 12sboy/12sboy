from datetime import datetime, timezone
import asyncio
import unittest

from trading_bot.telegram_monitor import (
    ConsoleTelegramClient,
    DemoReportProvider,
    TelegramMonitoringModule,
    TradeEntryEvent,
    TradeExitEvent,
    format_entry,
    format_exit,
    format_hourly_report,
)


class TelegramMonitorFormattingTests(unittest.TestCase):
    def test_entry_message_contains_required_fields(self):
        text = format_entry(TradeEntryEvent(
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=65200,
            quantity=0.05,
            quantity_unit="BTC",
            leverage=10,
            timestamp=datetime(2026, 6, 1, 14, 23, 5, tzinfo=timezone.utc),
        ))
        self.assertIn("[진입] LONG @ 65,200.00 USDT", text)
        self.assertIn("물량: 0.05 BTC", text)
        self.assertIn("레버리지: 10x", text)
        self.assertIn("시간: 2026-06-01 14:23:05", text)

    def test_exit_stop_loss_message_emphasizes_warning(self):
        text = format_exit(TradeExitEvent(
            symbol="ETH/USDT",
            direction="SHORT",
            exit_price=2030,
            entry_price=2010,
            quantity=0.5,
            realized_pnl_usdt=-10,
            realized_pnl_pct=-1.0,
            is_stop_loss=True,
            timestamp=datetime(2026, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
        ))
        self.assertIn("[청산] SHORT 종료", text)
        self.assertIn("실현 손익: -10.00 USDT (-1.0%)", text)
        self.assertIn("손절 여부: True", text)
        self.assertIn("⚠️", text)
        self.assertIn("손절 실행", text)
        self.assertIn("손실 금액: 10.00 USDT", text)

    def test_hourly_report_contains_market_account_and_pnl(self):
        async def run():
            provider = DemoReportProvider()
            return format_hourly_report(
                await provider.market_snapshots_1h(),
                await provider.account_snapshot(),
                await provider.realized_pnl_since(datetime.now(timezone.utc)),
            )
        text = asyncio.run(run())
        self.assertIn("[시간 리포트]", text)
        self.assertIn("시장 1시간봉", text)
        self.assertIn("계좌 평가잔고", text)
        self.assertIn("이번 시간 실현 손익", text)


class TelegramMonitorRuntimeTests(unittest.TestCase):
    def test_monitor_queues_and_sends_dry_run_messages(self):
        async def run():
            client = ConsoleTelegramClient()
            monitor = TelegramMonitoringModule(chat_id="dry", provider=DemoReportProvider(), client=client, merge_window_sec=0)
            await monitor.start()
            await monitor.notify_entry(TradeEntryEvent("BTC/USDT", "LONG", 65200, 0.05, "BTC", 10))
            await monitor.send_status()
            await monitor.stop()
            return client.messages
        messages = asyncio.run(run())
        self.assertEqual(len(messages), 2)
        self.assertIn("[진입]", messages[0])
        self.assertIn("[상태]", messages[1])

    def test_error_notification_is_rate_limit_safe_and_html_escaped(self):
        async def run():
            client = ConsoleTelegramClient()
            monitor = TelegramMonitoringModule(chat_id="dry", client=client, merge_window_sec=0)
            await monitor.start()
            await monitor.notify_error("bad <token>")
            await monitor.stop()
            return client.messages[0]
        text = asyncio.run(run())
        self.assertIn("[봇 오류]", text)
        self.assertIn("bad &lt;token&gt;", text)


if __name__ == "__main__":
    unittest.main()
