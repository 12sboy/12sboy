import unittest

from trading_bot.risk import RiskConfig, calculate_order_qty


class RiskTests(unittest.TestCase):
    def test_qty_uses_equity_risk_and_stop_distance(self):
        qty = calculate_order_qty(equity_usdt=1000, entry_price=100, stop_price=98, config=RiskConfig(risk_per_trade_pct=0.01, max_notional_pct=1.0))
        self.assertAlmostEqual(qty, 5.0)

    def test_qty_is_capped_by_max_notional(self):
        qty = calculate_order_qty(equity_usdt=1000, entry_price=100, stop_price=99.9, config=RiskConfig(risk_per_trade_pct=0.01, max_notional_pct=0.2, leverage=1))
        self.assertAlmostEqual(qty, 2.0)

    def test_leverage_expands_futures_notional_cap(self):
        qty = calculate_order_qty(equity_usdt=1000, entry_price=100, stop_price=99.9, config=RiskConfig(risk_per_trade_pct=0.01, max_notional_pct=0.2, leverage=5))
        self.assertAlmostEqual(qty, 10.0)

    def test_absolute_notional_cap_limits_demo_account_growth(self):
        qty = calculate_order_qty(
            equity_usdt=100000,
            entry_price=100,
            stop_price=99.9,
            config=RiskConfig(risk_per_trade_pct=0.01, max_notional_pct=1.0, leverage=5, max_order_notional_usdt=250),
        )
        self.assertAlmostEqual(qty, 2.5)

    def test_invalid_stop_returns_zero(self):
        self.assertEqual(calculate_order_qty(1000, 100, 100, RiskConfig()), 0)


if __name__ == "__main__":
    unittest.main()
