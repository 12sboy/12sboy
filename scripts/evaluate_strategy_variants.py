from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_2y import fetch_klines, enrich, pnl, SYMBOLS
from trading_bot.core import StrategyConfig
from trading_bot.risk import RiskConfig, calculate_order_qty

INITIAL_EQUITY = float(os.environ.get("INITIAL_EQUITY_PER_SYMBOL", "1000"))
LEVERAGE = float(os.environ.get("LEVERAGE", "5"))
MAKER_FEE = float(os.environ.get("MAKER_FEE", "0.0002"))
TAKER_FEE = float(os.environ.get("TAKER_FEE", "0.00055"))
OUT = Path(os.environ.get("EVAL_OUT", "reports/strategy_variant_evaluation.json"))
RISK = RiskConfig(risk_per_trade_pct=0.005, max_notional_pct=0.25, min_qty=0.0, leverage=LEVERAGE)


@dataclass
class Setup:
    side: str
    index: int


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def is_a_plus(vwap: float, e, cfg: StrategyConfig) -> bool:
    return any(level and abs(vwap - level) / level <= cfg.a_plus_alignment_pct for level in (e.pre_high, e.pre_low, e.prior_high, e.prior_low))


def run_symbol(symbol: str, candles, cfg: StrategyConfig) -> dict:
    data = enrich(candles)
    equity = INITIAL_EQUITY
    curve = [equity]
    position = None
    current = None
    setup: Setup | None = None
    trades = []
    for i in range(1, len(data)):
        prev, cur = data[i - 1], data[i]
        c = cur.c
        if cur.pre_high is None or cur.pre_low is None or cur.prior_high is None or cur.prior_low is None:
            continue
        if c.timestamp.hour < cfg.trade_start_hour_utc:
            continue
        if position:
            side = position["side"]
            action = None
            if side == "LONG":
                if c.close < cur.vwap:
                    action = ("vwap_stop_loss", 1.0)
                elif cfg.fixed_take_profit_pct is not None and c.close >= position["entry"] * (1 + cfg.fixed_take_profit_pct):
                    action = ("fixed_take_profit", 1.0)
                elif not position["scaled_out"] and c.close >= cur.ema8 * (1 + cfg.ema_space_take_profit_pct):
                    action = ("ema8_space_take_profit", cfg.take_profit_fraction)
                elif position["scaled_out"] and c.close < cur.ema8:
                    action = ("ema8_trailing_stop", 1.0)
            else:
                if c.close > cur.vwap:
                    action = ("vwap_stop_loss", 1.0)
                elif cfg.fixed_take_profit_pct is not None and c.close <= position["entry"] * (1 - cfg.fixed_take_profit_pct):
                    action = ("fixed_take_profit", 1.0)
                elif not position["scaled_out"] and c.close <= cur.ema8 * (1 - cfg.ema_space_take_profit_pct):
                    action = ("ema8_space_take_profit", cfg.take_profit_fraction)
                elif position["scaled_out"] and c.close > cur.ema8:
                    action = ("ema8_trailing_stop", 1.0)
            if action:
                reason, frac = action
                close_qty = position["qty"] * frac
                gross = pnl(side, position["entry"], c.close, close_qty)
                fee_share = position["entry_fee_remaining"] * (close_qty / position["qty"]) if position["qty"] else 0.0
                close_fee = c.close * close_qty * TAKER_FEE
                net = gross - fee_share - close_fee
                equity += net
                curve.append(equity)
                position["qty"] -= close_qty
                position["entry_fee_remaining"] -= fee_share
                current["exits"].append({"time": c.timestamp.isoformat(), "reason": reason, "net": net, "fee": fee_share + close_fee})
                if frac < 1.0:
                    position["scaled_out"] = True
                else:
                    current["pnl"] = sum(x["net"] for x in current["exits"])
                    current["exit_reason"] = reason
                    trades.append(current)
                    current = None
                    position = None
            continue

        if prev.c.close <= cur.pre_high and c.close > cur.pre_high * (1 + cfg.min_breakout_pct):
            setup = Setup("LONG", i)
        elif prev.c.close >= cur.pre_low and c.close < cur.pre_low * (1 - cfg.min_breakout_pct):
            setup = Setup("SHORT", i)
        if setup and i - setup.index > max(cfg.setup_max_age_bars, 1):
            setup = None
        if not setup:
            continue
        touches = c.low <= cur.vwap * (1 + cfg.retest_tolerance_pct) and c.high >= cur.vwap * (1 - cfg.retest_tolerance_pct)
        if not touches:
            continue
        if cfg.max_close_vwap_distance_pct and abs(c.close - cur.vwap) / cur.vwap > cfg.max_close_vwap_distance_pct:
            continue
        mom = data[i - 12].c.close if i >= 12 else None
        long_ok = setup.side == "LONG" and c.close >= cur.vwap
        short_ok = setup.side == "SHORT" and c.close <= cur.vwap
        if cfg.require_candle_direction:
            long_ok = long_ok and c.close > c.open
            short_ok = short_ok and c.close < c.open
        if cfg.require_momentum_1h:
            long_ok = long_ok and mom is not None and c.close > mom
            short_ok = short_ok and mom is not None and c.close < mom
        if cfg.require_ema_vwap_alignment:
            long_ok = long_ok and cur.ema8 >= cur.vwap
            short_ok = short_ok and cur.ema8 <= cur.vwap
        if not (long_ok or short_ok):
            continue
        side = "LONG" if long_ok else "SHORT"
        entry = cur.vwap
        stop = min(cur.vwap, c.low) if side == "LONG" else max(cur.vwap, c.high)
        ap = is_a_plus(cur.vwap, cur, cfg)
        qty = calculate_order_qty(equity, entry, stop if stop != entry else (c.low if side == "LONG" else c.high), RISK) * (cfg.a_plus_size_multiplier if ap else cfg.normal_size_multiplier)
        if qty <= 0:
            setup = None
            continue
        entry_fee = entry * qty * MAKER_FEE
        position = {"side": side, "entry": entry, "qty": qty, "scaled_out": False, "entry_fee_remaining": entry_fee}
        current = {"symbol": symbol, "side": side, "entry_time": c.timestamp.isoformat(), "entry": entry, "qty": qty, "a_plus": ap, "exits": []}
        setup = None
    if position and current:
        last = data[-1].c
        gross = pnl(position["side"], position["entry"], last.close, position["qty"])
        close_fee = last.close * position["qty"] * TAKER_FEE
        net = gross - close_fee - position["entry_fee_remaining"]
        equity += net
        curve.append(equity)
        current["exits"].append({"time": last.timestamp.isoformat(), "reason": "end", "net": net, "fee": close_fee + position["entry_fee_remaining"]})
        current["pnl"] = net
        current["exit_reason"] = "end"
        trades.append(current)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gp = sum(t["pnl"] for t in wins); gl = sum(t["pnl"] for t in losses)
    peak = INITIAL_EQUITY; max_dd = 0.0
    for e in curve:
        peak = max(peak, e); max_dd = min(max_dd, e - peak)
    return {
        "symbol": symbol, "initial_equity": INITIAL_EQUITY, "final_equity": round(equity, 6), "net_pnl": round(equity - INITIAL_EQUITY, 6), "net_return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 4),
        "trades": len(trades), "wins": len(wins), "losses": len(losses), "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "gross_profit": round(gp, 6), "gross_loss": round(gl, 6), "profit_factor": round(gp / abs(gl), 4) if gl else None, "max_drawdown_usdt": round(max_dd, 6), "largest_loss": round(min((t["pnl"] for t in trades), default=0), 6),
        "long_trades": sum(t["side"] == "LONG" for t in trades), "short_trades": sum(t["side"] == "SHORT" for t in trades), "sample_trades": trades[:3]
    }


def combine(results):
    net=sum(r["net_pnl"] for r in results.values()); tr=sum(r["trades"] for r in results.values()); w=sum(r["wins"] for r in results.values()); gp=sum(r["gross_profit"] for r in results.values()); gl=sum(r["gross_loss"] for r in results.values())
    return {"total_initial_equity": INITIAL_EQUITY*len(results), "total_final_equity": round(INITIAL_EQUITY*len(results)+net,6), "total_net_pnl": round(net,6), "total_return_pct": round(net/(INITIAL_EQUITY*len(results))*100,4), "total_trades": tr, "total_wins": w, "total_losses": tr-w, "win_rate_pct": round(w/tr*100,2) if tr else 0.0, "gross_profit": round(gp,6), "gross_loss": round(gl,6), "profit_factor": round(gp/abs(gl),4) if gl else None, "sum_symbol_max_dd": round(sum(r["max_drawdown_usdt"] for r in results.values()),6)}


def main():
    start=parse_dt(os.environ.get("BACKTEST_START", "2026-05-01T00:00:00+00:00")); end=parse_dt(os.environ.get("BACKTEST_END", "2026-06-05T06:00:00+00:00"))
    variants={
        "baseline_live": StrategyConfig(fixed_take_profit_pct=0.0025, require_candle_direction=True, setup_max_age_bars=10_000, trade_start_hour_utc=0),
        "conservative_filtered": StrategyConfig(retest_tolerance_pct=0.002, fixed_take_profit_pct=0.005, require_candle_direction=True, require_momentum_1h=True, require_ema_vwap_alignment=True, setup_max_age_bars=12, min_breakout_pct=0.0005, max_close_vwap_distance_pct=0.0015, trade_start_hour_utc=8),
        "filtered_no_fixed_tp": StrategyConfig(retest_tolerance_pct=0.002, fixed_take_profit_pct=None, require_candle_direction=True, require_momentum_1h=True, require_ema_vwap_alignment=True, setup_max_age_bars=12, min_breakout_pct=0.0005, max_close_vwap_distance_pct=0.0015, trade_start_hour_utc=8),
    }
    candles={}
    for s in SYMBOLS:
        print(f"Fetching {s} {start.isoformat()} -> {end.isoformat()}...", flush=True); candles[s]=fetch_klines(s,start,end); print(f"{s}: {len(candles[s])} candles", flush=True)
    report={"generated_at":datetime.now(timezone.utc).isoformat(),"period":{"start":start.isoformat(),"end":end.isoformat()},"assumptions":{"initial_equity_per_symbol":INITIAL_EQUITY,"fees":{"maker_entry":MAKER_FEE,"taker_exit":TAKER_FEE},"level_model":"pre-session 00-08 UTC, no trades before configured start"},"variants":{}}
    for name,cfg in variants.items():
        symres={s:run_symbol(s,rows,cfg) for s,rows in candles.items()}; report["variants"][name]={"config":asdict(cfg),"combined":combine(symres),"symbols":symres}
        print(name, json.dumps(report["variants"][name]["combined"], ensure_ascii=False))
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(f"Saved {OUT}")

if __name__ == "__main__": main()
