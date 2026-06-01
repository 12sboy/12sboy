import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from trading_bot.exchange import BybitDemoClient, _fmt, _infer_bybit_position_idx, normalize_bybit_symbol


class BybitDemoClientTests(unittest.TestCase):
    def test_normalize_bybit_symbol(self):
        self.assertEqual(normalize_bybit_symbol("BTC/USDT"), "BTCUSDT")
        self.assertEqual(normalize_bybit_symbol("ETH/USDT:USDT"), "ETHUSDT")

    def test_sign_get_request_uses_bybit_v5_payload_format(self):
        client = BybitDemoClient(api_key="key", api_secret="secret", recv_window="5000", clock=lambda: 1700000000000)
        query = "accountType=UNIFIED&coin=USDT"
        expected_payload = "1700000000000key5000" + query
        expected = hmac.new(b"secret", expected_payload.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(client.sign(query), expected)

    def test_place_limit_order_posts_demo_endpoint_payload(self):
        captured = {}

        def fake_request(method, path, body=None, query=None, private=False):
            captured.update({"method": method, "path": path, "body": body, "query": query, "private": private})
            return {"retCode": 0, "result": {"orderId": "abc"}}

        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._instrument_cache["BTCUSDT"] = {
            "lotSizeFilter": {"minOrderQty": "0.001", "qtyStep": "0.001", "minNotionalValue": "5"},
            "priceFilter": {"tickSize": "0.1"},
        }
        client._request = fake_request
        result = client.place_order("BTC/USDT", "Buy", 0.01, order_type="Limit", price=50000, reduce_only=True)
        self.assertEqual(result["orderId"], "abc")
        self.assertEqual(captured["path"], "/v5/order/create")
        self.assertTrue(captured["private"])
        self.assertEqual(captured["body"]["category"], "linear")
        self.assertEqual(captured["body"]["symbol"], "BTCUSDT")
        self.assertEqual(captured["body"]["orderType"], "Limit")
        self.assertEqual(captured["body"]["price"], "50000")
        self.assertEqual(captured["body"]["positionIdx"], 1)
        self.assertTrue(captured["body"]["reduceOnly"])

    def test_fetch_balance_extracts_usdt_equity(self):
        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._request = lambda *a, **k: {"retCode": 0, "result": {"list": [{"coin": [{"coin": "USDT", "equity": "123.45", "walletBalance": "120"}]}]}}
        balance = client.fetch_balance()
        self.assertEqual(balance["USDT"]["equity"], 123.45)

    def test_set_leverage_posts_buy_and_sell_leverage(self):
        captured = {}

        def fake_request(method, path, body=None, query=None, private=False, ok_ret_codes=None):
            captured.update({"method": method, "path": path, "body": body, "private": private, "ok_ret_codes": ok_ret_codes})
            return {"retCode": 0, "result": {}}

        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._request = fake_request
        client.set_leverage("ETH/USDT", 5)
        self.assertEqual(captured["path"], "/v5/position/set-leverage")
        self.assertEqual(captured["body"]["symbol"], "ETHUSDT")
        self.assertEqual(captured["body"]["buyLeverage"], "5")
        self.assertEqual(captured["body"]["sellLeverage"], "5")
        self.assertTrue(captured["private"])
        self.assertEqual(captured["ok_ret_codes"], {110043})

    def test_set_leverage_treats_already_set_as_success(self):
        def fake_request(method, path, body=None, query=None, private=False, ok_ret_codes=None):
            return {"retCode": 110043, "retMsg": "leverage not modified", "result": {}}

        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._request = fake_request
        result = client.set_leverage("BTC/USDT", 5)
        self.assertEqual(result, {"already_set": True})

    def test_normalize_order_values_uses_bybit_qty_step_and_tick_size(self):
        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._instrument_cache["ETHUSDT"] = {
            "lotSizeFilter": {"minOrderQty": "0.01", "qtyStep": "0.01", "minNotionalValue": "5"},
            "priceFilter": {"tickSize": "0.01"},
        }
        qty, price = client.normalize_order_values("ETH/USDT", 0.6224479633502639, 2010.606644643124)
        self.assertEqual(qty, "0.62")
        self.assertEqual(price, "2010.6")

    def test_normalize_order_values_rejects_too_small_qty(self):
        client = BybitDemoClient(api_key="key", api_secret="secret")
        client._instrument_cache["BTCUSDT"] = {
            "lotSizeFilter": {"minOrderQty": "0.001", "qtyStep": "0.001", "minNotionalValue": "5"},
            "priceFilter": {"tickSize": "0.1"},
        }
        with self.assertRaisesRegex(ValueError, "below Bybit minimum"):
            client.normalize_order_values("BTC/USDT", 0.0009, 70000.12)

    def test_fmt_preserves_small_quantized_decimals(self):
        self.assertEqual(_fmt(0.001), "0.001")
        self.assertEqual(_fmt(5.0), "5")

    def test_infer_bybit_position_idx_for_hedge_mode_entries(self):
        self.assertEqual(_infer_bybit_position_idx("Buy"), 1)
        self.assertEqual(_infer_bybit_position_idx("Sell"), 2)


if __name__ == "__main__":
    unittest.main()
