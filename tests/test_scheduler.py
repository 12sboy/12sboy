import unittest
from datetime import datetime, timedelta, timezone

from trading_bot.scheduler import HourlyGate


class SchedulerTests(unittest.TestCase):
    def test_hourly_gate_fires_once_per_hour(self):
        gate = HourlyGate()
        t = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        self.assertTrue(gate.should_fire(t))
        self.assertFalse(gate.should_fire(t + timedelta(minutes=30)))
        self.assertTrue(gate.should_fire(t + timedelta(hours=1)))


if __name__ == "__main__":
    unittest.main()
