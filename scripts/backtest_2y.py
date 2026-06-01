from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_bot.core import Candle, StrategyConfig
from trading_bot.risk import RiskConfig, calculate_order_qty

BASE_URL = "https://api.bybit.com"
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAME = "5m"
INTERVAL_MINUTES = 5
INITIAL_EQUITY = float(os.environ.get("INITIAL_EQUITY_PER_SYMBOL", "2000"))
LOOKBACK_DAYS = 365 * 2
OUT = Path(os.environ.get("BACKTEST_OUT", "reports/backtest_2y_report.json"))

STRATEGY = StrategyConfig(
    ema_length=8,
    retest_tolerance_pct=float(os.environ.get("RETEST_TOLERANCE_PCT", "0.002")),
    a_plus_alignment_pct=0.003,
    a_plus_size_multiplier=1.5,
    normal_size_multiplier=1.0,
    ema_space_take_profit_pct=0.025,
    take_profit_fraction=0.5,
    fixed_take_profit_pct=float(os.environ.get("FIXED_TAKE_PROFIT_PCT", "0.0025")),
    require_candle_direction=os.environ.get("REQUIRE_CANDLE_DIRECTION", "true").lower() not in {"0", "false", "no"},
)
RISK = RiskConfig(risk_per_trade_pct=0.005, max_notional_pct=0.25, min_qty=0.0, leverage=float(os.environ.get("LEVERAGE", "5")))


@dataclass
class Enriched:
    c: Candle
    ema8: float
    vwap: float
    pre_high: float | None
    pre_low: float | None
    prior_high: float | None
    prior_low: float | None


def normalize(symbol: str) -> str:
    return symbol.replace("/", "").replace(":USDT", "").upper()


def fetch_klines(symbol: str, start: datetime, end: datetime) -> list[Candle]:
    rows: list[list[str]] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    step_ms = INTERVAL_MINUTES * 60 * 1000 * 1000  # 1000 candles per request
    while cursor < end_ms:
        req_end = min(cursor + step_ms - 1, end_ms)
        params = urllib.parse.urlencode(
            {
                "category": "linear",
                "symbol": normalize(symbol),
                "interval": str(INTERVAL_MINUTES),
                "start": str(cursor),
                "end": str(req_end),
                "limit": "1000",
            }
        )
        url = f"{BASE_URL}/v5/market/kline?{params}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error for {symbol}: {payload}")
        batch = payload.get("result", {}).get("list", [])
        if batch:
            rows.extend(batch)
            oldest = min(int(r[0]) for r in batch)
            newest = max(int(r[0]) for r in batch)
            cursor = newest + INTERVAL_MINUTES * 60 * 1000
        else:
            cursor = req_end + 1
        time.sleep(0.03)
    by_ts: dict[int, Candle] = {}
    for row in rows:
        ts_ms, open_, high, low, close, volume, *_ = row
        ts = int(ts_ms)
        by_ts[ts] = Candle(
            datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
            float(open_),
            float(high),
            float(low),
            float(close),
            float(volume),
        )
    return [by_ts[k] for k in sorted(by_ts)]


def enrich(candles: list[Candle]) -> list[Enriched]:
    # daily stats first
    daily: dict[object, dict[str, float]] = {}
    pre: dict[object, dict[str, float]] = {}
    for c in candles:
        d = c.timestamp.date()
        daily.setdefault(d, {"high": -math.inf, "low": math.inf})
        daily[d]["high"] = max(daily[d]["high"], c.high)
        daily[d]["low"] = min(daily[d]["low"], c.low)
        if 0 <= c.timestamp.hour < 8:
            pre.setdefault(d, {"high": -math.inf, "low": math.inf})
            pre[d]["high"] = max(pre[d]["high"], c.high)
            pre[d]["low"] = min(pre[d]["low"], c.low)
    days = sorted(daily)
    prior_by_day = {}
    for i, d in enumerate(days):
        if i > 0:
            prior_by_day[d] = daily[days[i - 1]]

    out: list[Enriched] = []
    alpha = 2 / (STRATEGY.ema_length + 1)
    ema = None
    current_day = None
    cum_pv = 0.0
    cum_vol = 0.0
    for c in candles:
        d = c.timestamp.date()
        if d != current_day:
            current_day = d
            cum_pv = 0.0
            cum_vol = 0.0
        ema = c.close if ema is None else alpha * c.close + (1 - alpha) * ema
        typical = (c.high + c.low + c.close) / 3
        cum_pv += typical * c.volume
        cum_vol += c.volume
        vwap = cum_pv / cum_vol if cum_vol else typical
        p = pre.get(d)
        prior = prior_by_day.get(d)
        out.append(
            Enriched(
                c=c,
                ema8=ema,
                vwap=vwap,
                pre_high=p["high"] if p else None,
                pre_low=p["low"] if p else None,
                prior_high=prior["high"] if prior else None,
                prior_low=prior["low"] if prior else None,
            )
        )
    return out


def a_plus(vwap: float, e: Enriched) -> bool:
    for level in (e.pre_high, e.pre_low, e.prior_high, e.prior_low):
        if level and abs(vwap - level) / level <= STRATEGY.a_plus_alignment_pct:
            return True
    return False


def pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    return (exit_price - entry) * qty if side == "LONG" else (entry - exit_price) * qty


def run_backtest(symbol: str, candles: list[Candle]) -> dict:
    data = enrich(candles)
    equity = INITIAL_EQUITY
    equity_curve = [equity]
    position = None
    trades = []
    long_setup = False
    short_setup = False
    current_trade = None

    for i in range(1, len(data)):
        prev, cur = data[i - 1], data[i]
        c = cur.c
        if cur.pre_high is None or cur.pre_low is None or cur.prior_high is None or cur.prior_low is None:
            continue

        if position:
            side = position["side"]
            if side == "LONG":
                action = None
                if c.close < cur.vwap:
                    action = ("CLOSE", "vwap_stop_loss", 1.0)
                elif STRATEGY.fixed_take_profit_pct is not None and c.close >= position["entry"] * (1 + STRATEGY.fixed_take_profit_pct):
                    action = ("CLOSE", "fixed_take_profit", 1.0)
                elif not position["scaled_out"] and c.close >= cur.ema8 * (1 + STRATEGY.ema_space_take_profit_pct):
                    action = ("TAKE_PROFIT", "ema8_space_take_profit", STRATEGY.take_profit_fraction)
                elif position["scaled_out"] and c.close < cur.ema8:
                    action = ("CLOSE", "ema8_trailing_stop", 1.0)
            else:
                action = None
                if c.close > cur.vwap:
                    action = ("CLOSE", "vwap_stop_loss", 1.0)
                elif STRATEGY.fixed_take_profit_pct is not None and c.close <= position["entry"] * (1 - STRATEGY.fixed_take_profit_pct):
                    action = ("CLOSE", "fixed_take_profit", 1.0)
                elif not position["scaled_out"] and c.close <= cur.ema8 * (1 - STRATEGY.ema_space_take_profit_pct):
                    action = ("TAKE_PROFIT", "ema8_space_take_profit", STRATEGY.take_profit_fraction)
                elif position["scaled_out"] and c.close > cur.ema8:
                    action = ("CLOSE", "ema8_trailing_stop", 1.0)

            if action:
                kind, reason, frac = action
                close_qty = position["qty"] * frac if kind == "TAKE_PROFIT" else position["qty"]
                part_pnl = pnl(side, position["entry"], c.close, close_qty)
                equity += part_pnl
                equity_curve.append(equity)
                current_trade["exits"].append({"time": c.timestamp.isoformat(), "price": c.close, "qty": close_qty, "pnl": part_pnl, "reason": reason})
                position["qty"] -= close_qty
                if kind == "TAKE_PROFIT":
                    position["scaled_out"] = True
                else:
                    current_trade["exit_time"] = c.timestamp.isoformat()
                    current_trade["exit_price"] = c.close
                    current_trade["pnl"] = sum(x["pnl"] for x in current_trade["exits"])
                    current_trade["return_pct_on_initial_equity"] = current_trade["pnl"] / INITIAL_EQUITY * 100
                    trades.append(current_trade)
                    current_trade = None
                    position = None
            continue

        # Setup detection: previous candle closed beyond the pre-session level, current retests VWAP.
        if prev.c.close > cur.pre_high:
            long_setup = True
        if prev.c.close < cur.pre_low:
            short_setup = True
        touches = c.low <= cur.vwap * (1 + STRATEGY.retest_tolerance_pct) and c.high >= cur.vwap * (1 - STRATEGY.retest_tolerance_pct)

        long_candle_ok = not STRATEGY.require_candle_direction or c.close > c.open
        short_candle_ok = not STRATEGY.require_candle_direction or c.close < c.open

        if long_setup and touches and c.close > cur.vwap and long_candle_ok:
            entry = cur.vwap
            stop = min(cur.vwap, c.low)
            ap = a_plus(cur.vwap, cur)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.low, RISK) * (STRATEGY.a_plus_size_multiplier if ap else 1.0)
            if qty > 0:
                position = {"side": "LONG", "qty": qty, "entry": entry, "scaled_out": False}
                current_trade = {"symbol": symbol, "side": "LONG", "entry_time": c.timestamp.isoformat(), "entry_price": entry, "initial_qty": qty, "a_plus": ap, "exits": []}
            long_setup = False
            short_setup = False
        elif short_setup and touches and c.close < cur.vwap and short_candle_ok:
            entry = cur.vwap
            stop = max(cur.vwap, c.high)
            ap = a_plus(cur.vwap, cur)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.high, RISK) * (STRATEGY.a_plus_size_multiplier if ap else 1.0)
            if qty > 0:
                position = {"side": "SHORT", "qty": qty, "entry": entry, "scaled_out": False}
                current_trade = {"symbol": symbol, "side": "SHORT", "entry_time": c.timestamp.isoformat(), "entry_price": entry, "initial_qty": qty, "a_plus": ap, "exits": []}
            long_setup = False
            short_setup = False

    if position and current_trade:
        last = data[-1].c
        final_pnl = pnl(position["side"], position["entry"], last.close, position["qty"])
        equity += final_pnl
        equity_curve.append(equity)
        current_trade["exits"].append({"time": last.timestamp.isoformat(), "price": last.close, "qty": position["qty"], "pnl": final_pnl, "reason": "end_of_backtest"})
        current_trade["exit_time"] = last.timestamp.isoformat()
        current_trade["exit_price"] = last.close
        current_trade["pnl"] = sum(x["pnl"] for x in current_trade["exits"])
        current_trade["return_pct_on_initial_equity"] = current_trade["pnl"] / INITIAL_EQUITY * 100
        trades.append(current_trade)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    peak = INITIAL_EQUITY
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)
    return {
        "symbol": symbol,
        "period": {"start": candles[0].timestamp.isoformat(), "end": candles[-1].timestamp.isoformat(), "candles": len(candles), "timeframe": TIMEFRAME},
        "initial_equity": INITIAL_EQUITY,
        "final_equity": round(equity, 6),
        "net_pnl": round(equity - INITIAL_EQUITY, 6),
        "net_return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 4),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(trades) - len(wins) - len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(gross_profit / abs(gross_loss), 4) if gross_loss else None,
        "avg_win": round(gross_profit / len(wins), 6) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 6) if losses else 0.0,
        "largest_win": round(max((t["pnl"] for t in trades), default=0), 6),
        "largest_loss": round(min((t["pnl"] for t in trades), default=0), 6),
        "max_drawdown_usdt": round(max_dd, 6),
        "max_drawdown_pct_initial": round(max_dd / INITIAL_EQUITY * 100, 4),
        "a_plus_trades": sum(1 for t in trades if t["a_plus"]),
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "sample_trades": trades[:3],
    }


def combine(results: dict[str, dict]) -> dict:
    total_net = sum(r["net_pnl"] for r in results.values())
    total_trades = sum(r["trades"] for r in results.values())
    total_wins = sum(r["wins"] for r in results.values())
    total_losses = sum(r["losses"] for r in results.values())
    gross_profit = sum(r["gross_profit"] for r in results.values())
    gross_loss = sum(r["gross_loss"] for r in results.values())
    return {
        "initial_equity_note": f"각 심볼별 {INITIAL_EQUITY:g} USDT 독립 백테스트. combined는 단순 합산.",
        "total_initial_equity": INITIAL_EQUITY * len(results),
        "total_final_equity": round(INITIAL_EQUITY * len(results) + total_net, 6),
        "total_net_pnl": round(total_net, 6),
        "total_return_pct": round(total_net / (INITIAL_EQUITY * len(results)) * 100, 4),
        "total_trades": total_trades,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "win_rate_pct": round(total_wins / total_trades * 100, 2) if total_trades else 0,
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(gross_profit / abs(gross_loss), 4) if gross_loss else None,
    }


def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    results = {}
    for symbol in SYMBOLS:
        print(f"Fetching {symbol} {start.isoformat()} -> {end.isoformat()}...", flush=True)
        candles = fetch_klines(symbol, start, end)
        print(f"Backtesting {symbol}: {len(candles)} candles", flush=True)
        results[symbol] = run_backtest(symbol, candles)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assumptions": {
            "initial_equity_per_symbol": INITIAL_EQUITY,
            "timeframe": TIMEFRAME,
            "lookback_days": LOOKBACK_DAYS,
            "fees_slippage": "not included",
            "position_sizing": {"risk_per_trade_pct": RISK.risk_per_trade_pct, "max_notional_pct": RISK.max_notional_pct, "leverage": RISK.leverage},
            "strategy": {
                "retest_tolerance_pct": STRATEGY.retest_tolerance_pct,
                "fixed_take_profit_pct": STRATEGY.fixed_take_profit_pct,
                "require_candle_direction": STRATEGY.require_candle_direction,
                "stop_loss": "VWAP close break",
            },
        },
        "combined": combine(results),
        "symbols": results,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["combined"], ensure_ascii=False, indent=2))
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
