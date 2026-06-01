from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_2y import fetch_klines, enrich, pnl, SYMBOLS, TIMEFRAME
from trading_bot.risk import RiskConfig, calculate_order_qty

INITIAL_EQUITY = float(os.environ.get("INITIAL_EQUITY_PER_SYMBOL", "1000"))
LEVERAGE = float(os.environ.get("LEVERAGE", "5"))
OUT = Path(os.environ.get("OPT_OUT", "reports/optimization_2026_jan_may_5x.json"))


@dataclass(frozen=True)
class Variant:
    name: str
    retest_tolerance_pct: float = 0.0015
    a_plus_alignment_pct: float = 0.003
    a_plus_size_multiplier: float = 1.5
    ema_space_take_profit_pct: float = 0.025
    take_profit_fraction: float = 0.5
    a_plus_only: bool = False
    require_candle_direction: bool = False
    require_momentum_1h: bool = False
    trade_after_pre_session: bool = True
    min_vwap_deviation_pct: float = 0.0


RISK = RiskConfig(risk_per_trade_pct=0.005, max_notional_pct=0.25, min_qty=0.0, leverage=LEVERAGE)


def parse_dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def is_a_plus(vwap: float, e, cfg: Variant) -> bool:
    for level in (e.pre_high, e.pre_low, e.prior_high, e.prior_low):
        if level and abs(vwap - level) / level <= cfg.a_plus_alignment_pct:
            return True
    return False


def summarize(symbol: str, candles, cfg: Variant) -> dict:
    data = enrich(candles)
    equity = INITIAL_EQUITY
    equity_curve = [equity]
    position = None
    current_trade = None
    trades = []
    long_setup = False
    short_setup = False

    for i in range(1, len(data)):
        prev, cur = data[i - 1], data[i]
        c = cur.c
        if cur.pre_high is None or cur.pre_low is None or cur.prior_high is None or cur.prior_low is None:
            continue
        if cfg.trade_after_pre_session and c.timestamp.hour < 8:
            continue

        if position:
            side = position["side"]
            action = None
            if side == "LONG":
                if c.close < cur.vwap:
                    action = ("CLOSE", "vwap_stop_loss", 1.0)
                elif not position["scaled_out"] and c.close >= cur.ema8 * (1 + cfg.ema_space_take_profit_pct):
                    action = ("TAKE_PROFIT", "ema8_space_take_profit", cfg.take_profit_fraction)
                elif position["scaled_out"] and c.close < cur.ema8:
                    action = ("CLOSE", "ema8_trailing_stop", 1.0)
            else:
                if c.close > cur.vwap:
                    action = ("CLOSE", "vwap_stop_loss", 1.0)
                elif not position["scaled_out"] and c.close <= cur.ema8 * (1 - cfg.ema_space_take_profit_pct):
                    action = ("TAKE_PROFIT", "ema8_space_take_profit", cfg.take_profit_fraction)
                elif position["scaled_out"] and c.close > cur.ema8:
                    action = ("CLOSE", "ema8_trailing_stop", 1.0)
            if action:
                kind, reason, frac = action
                close_qty = position["qty"] * frac if kind == "TAKE_PROFIT" else position["qty"]
                part_pnl = pnl(side, position["entry"], c.close, close_qty)
                equity += part_pnl
                equity_curve.append(equity)
                current_trade["exits"].append({"pnl": part_pnl, "reason": reason})
                position["qty"] -= close_qty
                if kind == "TAKE_PROFIT":
                    position["scaled_out"] = True
                else:
                    current_trade["pnl"] = sum(x["pnl"] for x in current_trade["exits"])
                    trades.append(current_trade)
                    current_trade = None
                    position = None
            continue

        if prev.c.close > cur.pre_high:
            long_setup = True
        if prev.c.close < cur.pre_low:
            short_setup = True

        touches = c.low <= cur.vwap * (1 + cfg.retest_tolerance_pct) and c.high >= cur.vwap * (1 - cfg.retest_tolerance_pct)
        ap = is_a_plus(cur.vwap, cur, cfg)
        if cfg.a_plus_only and not ap:
            continue
        if cfg.min_vwap_deviation_pct and abs(c.close - cur.vwap) / cur.vwap < cfg.min_vwap_deviation_pct:
            continue

        momentum_close = data[i - 12].c.close if i >= 12 else None
        long_momentum_ok = not cfg.require_momentum_1h or (momentum_close is not None and c.close > momentum_close)
        short_momentum_ok = not cfg.require_momentum_1h or (momentum_close is not None and c.close < momentum_close)
        long_candle_ok = not cfg.require_candle_direction or c.close > c.open
        short_candle_ok = not cfg.require_candle_direction or c.close < c.open

        if long_setup and touches and c.close > cur.vwap and long_momentum_ok and long_candle_ok:
            entry = cur.vwap
            stop = min(cur.vwap, c.low)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.low, RISK) * (cfg.a_plus_size_multiplier if ap else 1.0)
            if qty > 0:
                position = {"side": "LONG", "qty": qty, "entry": entry, "scaled_out": False}
                current_trade = {"symbol": symbol, "side": "LONG", "a_plus": ap, "exits": []}
            long_setup = False
            short_setup = False
        elif short_setup and touches and c.close < cur.vwap and short_momentum_ok and short_candle_ok:
            entry = cur.vwap
            stop = max(cur.vwap, c.high)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.high, RISK) * (cfg.a_plus_size_multiplier if ap else 1.0)
            if qty > 0:
                position = {"side": "SHORT", "qty": qty, "entry": entry, "scaled_out": False}
                current_trade = {"symbol": symbol, "side": "SHORT", "a_plus": ap, "exits": []}
            long_setup = False
            short_setup = False

    if position and current_trade:
        last = data[-1].c
        final_pnl = pnl(position["side"], position["entry"], last.close, position["qty"])
        equity += final_pnl
        equity_curve.append(equity)
        current_trade["exits"].append({"pnl": final_pnl, "reason": "end_of_backtest"})
        current_trade["pnl"] = sum(x["pnl"] for x in current_trade["exits"])
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
        "initial_equity": INITIAL_EQUITY,
        "final_equity": round(equity, 6),
        "net_pnl": round(equity - INITIAL_EQUITY, 6),
        "net_return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 4),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(gross_profit / abs(gross_loss), 4) if gross_loss else None,
        "avg_win": round(gross_profit / len(wins), 6) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 6) if losses else 0,
        "largest_win": round(max((t["pnl"] for t in trades), default=0), 6),
        "largest_loss": round(min((t["pnl"] for t in trades), default=0), 6),
        "max_drawdown_usdt": round(max_dd, 6),
        "max_drawdown_pct_initial": round(max_dd / INITIAL_EQUITY * 100, 4),
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "a_plus_trades": sum(1 for t in trades if t["a_plus"]),
    }


def combine(results: dict[str, dict]) -> dict:
    total_net = sum(r["net_pnl"] for r in results.values())
    trades = sum(r["trades"] for r in results.values())
    wins = sum(r["wins"] for r in results.values())
    losses = sum(r["losses"] for r in results.values())
    gp = sum(r["gross_profit"] for r in results.values())
    gl = sum(r["gross_loss"] for r in results.values())
    max_dd = sum(r["max_drawdown_usdt"] for r in results.values())
    return {
        "total_initial_equity": INITIAL_EQUITY * len(results),
        "total_final_equity": round(INITIAL_EQUITY * len(results) + total_net, 6),
        "total_net_pnl": round(total_net, 6),
        "total_return_pct": round(total_net / (INITIAL_EQUITY * len(results)) * 100, 4),
        "total_trades": trades,
        "total_wins": wins,
        "total_losses": losses,
        "win_rate_pct": round(wins / trades * 100, 2) if trades else 0,
        "gross_profit": round(gp, 6),
        "gross_loss": round(gl, 6),
        "profit_factor": round(gp / abs(gl), 4) if gl else None,
        "sum_symbol_max_dd": round(max_dd, 6),
    }


def main() -> None:
    start = parse_dt(os.environ.get("BACKTEST_START", "2026-01-01T00:00:00+00:00"))
    end = parse_dt(os.environ.get("BACKTEST_END", "2026-06-01T00:00:00+00:00"))
    candles_by_symbol = {}
    for symbol in SYMBOLS:
        print(f"Fetching {symbol} {start.isoformat()} -> {end.isoformat()}...", flush=True)
        candles_by_symbol[symbol] = fetch_klines(symbol, start, end)
        print(f"{symbol}: {len(candles_by_symbol[symbol])} candles", flush=True)

    variants = []
    for tol in [0.0005, 0.001, 0.0015, 0.002]:
        for tp in [0.015, 0.02, 0.025, 0.03]:
            variants.append(Variant(name=f"base_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp))
            variants.append(Variant(name=f"dir_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp, require_candle_direction=True))
            variants.append(Variant(name=f"mom_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp, require_momentum_1h=True))
            variants.append(Variant(name=f"dir_mom_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp, require_candle_direction=True, require_momentum_1h=True))
            variants.append(Variant(name=f"aplus_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp, a_plus_only=True))
            variants.append(Variant(name=f"aplus_dir_tol{tol}_tp{tp}", retest_tolerance_pct=tol, ema_space_take_profit_pct=tp, a_plus_only=True, require_candle_direction=True))

    runs = []
    for cfg in variants:
        symbol_results = {symbol: summarize(symbol, candles, cfg) for symbol, candles in candles_by_symbol.items()}
        combined = combine(symbol_results)
        runs.append({"variant": cfg.__dict__, "combined": combined, "symbols": symbol_results})
    # score: prefer winrate >= 45, then pnl, with trade count floor
    candidates = [r for r in runs if r["combined"]["total_trades"] >= 80]
    top_by_score = sorted(candidates, key=lambda r: (r["combined"]["win_rate_pct"] >= 45, r["combined"]["total_net_pnl"], r["combined"]["win_rate_pct"]), reverse=True)[:20]
    top_by_win = sorted(candidates, key=lambda r: (r["combined"]["win_rate_pct"], r["combined"]["total_net_pnl"]), reverse=True)[:20]
    top_by_pnl = sorted(candidates, key=lambda r: (r["combined"]["total_net_pnl"], r["combined"]["win_rate_pct"]), reverse=True)[:20]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "assumptions": {"initial_equity_per_symbol": INITIAL_EQUITY, "total_initial_equity": INITIAL_EQUITY * len(SYMBOLS), "leverage": LEVERAGE, "timeframe": TIMEFRAME, "fees_slippage_funding": "not included"},
        "top_by_score": top_by_score,
        "top_by_winrate": top_by_win,
        "top_by_pnl": top_by_pnl,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("TOP SCORE")
    for r in top_by_score[:10]:
        c = r["combined"]
        print(r["variant"]["name"], "pnl", c["total_net_pnl"], "ret", c["total_return_pct"], "win", c["win_rate_pct"], "trades", c["total_trades"], "pf", c["profit_factor"], "ddsum", c["sum_symbol_max_dd"])
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
