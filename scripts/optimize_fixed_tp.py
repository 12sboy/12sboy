from __future__ import annotations

import json
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
OUT = Path(os.environ.get("OPT_OUT", "reports/optimization_fixed_tp_2026_jan_may_5x.json"))
RISK = RiskConfig(risk_per_trade_pct=0.005, max_notional_pct=0.25, min_qty=0.0, leverage=LEVERAGE)


@dataclass(frozen=True)
class Variant:
    name: str
    retest_tolerance_pct: float = 0.0015
    a_plus_alignment_pct: float = 0.003
    a_plus_size_multiplier: float = 1.5
    fixed_tp_pct: float = 0.005
    a_plus_only: bool = False
    require_candle_direction: bool = False
    require_momentum_1h: bool = False
    trade_after_pre_session: bool = True


def parse_dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def is_a_plus(vwap: float, e, cfg: Variant) -> bool:
    return any(level and abs(vwap - level) / level <= cfg.a_plus_alignment_pct for level in (e.pre_high, e.pre_low, e.prior_high, e.prior_low))


def summarize(symbol: str, candles, cfg: Variant) -> dict:
    data = enrich(candles)
    equity = INITIAL_EQUITY
    curve = [equity]
    position = None
    trades = []
    long_setup = short_setup = False
    current = None
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
                    action = "vwap_stop_loss"
                elif c.close >= position["entry"] * (1 + cfg.fixed_tp_pct):
                    action = "fixed_take_profit"
            else:
                if c.close > cur.vwap:
                    action = "vwap_stop_loss"
                elif c.close <= position["entry"] * (1 - cfg.fixed_tp_pct):
                    action = "fixed_take_profit"
            if action:
                part = pnl(side, position["entry"], c.close, position["qty"])
                equity += part
                curve.append(equity)
                current["pnl"] = part
                current["exit_reason"] = action
                trades.append(current)
                position = None
                current = None
            continue
        if prev.c.close > cur.pre_high:
            long_setup = True
        if prev.c.close < cur.pre_low:
            short_setup = True
        touches = c.low <= cur.vwap * (1 + cfg.retest_tolerance_pct) and c.high >= cur.vwap * (1 - cfg.retest_tolerance_pct)
        ap = is_a_plus(cur.vwap, cur, cfg)
        if cfg.a_plus_only and not ap:
            continue
        mom = data[i - 12].c.close if i >= 12 else None
        long_ok = (not cfg.require_candle_direction or c.close > c.open) and (not cfg.require_momentum_1h or (mom is not None and c.close > mom))
        short_ok = (not cfg.require_candle_direction or c.close < c.open) and (not cfg.require_momentum_1h or (mom is not None and c.close < mom))
        if long_setup and touches and c.close > cur.vwap and long_ok:
            entry = cur.vwap
            stop = min(cur.vwap, c.low)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.low, RISK) * (cfg.a_plus_size_multiplier if ap else 1)
            if qty > 0:
                position = {"side": "LONG", "entry": entry, "qty": qty}
                current = {"side": "LONG", "a_plus": ap}
            long_setup = short_setup = False
        elif short_setup and touches and c.close < cur.vwap and short_ok:
            entry = cur.vwap
            stop = max(cur.vwap, c.high)
            qty = calculate_order_qty(equity, entry, stop if stop != entry else c.high, RISK) * (cfg.a_plus_size_multiplier if ap else 1)
            if qty > 0:
                position = {"side": "SHORT", "entry": entry, "qty": qty}
                current = {"side": "SHORT", "a_plus": ap}
            long_setup = short_setup = False
    if position and current:
        last = data[-1].c
        part = pnl(position["side"], position["entry"], last.close, position["qty"])
        equity += part
        curve.append(equity)
        current["pnl"] = part
        current["exit_reason"] = "end"
        trades.append(current)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gp = sum(t["pnl"] for t in wins); gl = sum(t["pnl"] for t in losses)
    peak = INITIAL_EQUITY; max_dd = 0
    for e in curve:
        peak = max(peak, e); max_dd = min(max_dd, e - peak)
    return {
        "symbol": symbol, "initial_equity": INITIAL_EQUITY, "final_equity": round(equity, 6), "net_pnl": round(equity - INITIAL_EQUITY, 6),
        "net_return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 4), "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0, "gross_profit": round(gp, 6), "gross_loss": round(gl, 6),
        "profit_factor": round(gp / abs(gl), 4) if gl else None, "avg_win": round(gp/len(wins), 6) if wins else 0, "avg_loss": round(gl/len(losses), 6) if losses else 0,
        "largest_win": round(max((t["pnl"] for t in trades), default=0), 6), "largest_loss": round(min((t["pnl"] for t in trades), default=0), 6),
        "max_drawdown_usdt": round(max_dd, 6), "max_drawdown_pct_initial": round(max_dd / INITIAL_EQUITY * 100, 4),
        "long_trades": sum(t["side"] == "LONG" for t in trades), "short_trades": sum(t["side"] == "SHORT" for t in trades), "a_plus_trades": sum(t["a_plus"] for t in trades),
    }


def combine(results):
    net = sum(r["net_pnl"] for r in results.values()); tr=sum(r["trades"] for r in results.values()); w=sum(r["wins"] for r in results.values()); l=sum(r["losses"] for r in results.values()); gp=sum(r["gross_profit"] for r in results.values()); gl=sum(r["gross_loss"] for r in results.values()); dd=sum(r["max_drawdown_usdt"] for r in results.values())
    return {"total_initial_equity": INITIAL_EQUITY*len(results), "total_final_equity": round(INITIAL_EQUITY*len(results)+net,6), "total_net_pnl": round(net,6), "total_return_pct": round(net/(INITIAL_EQUITY*len(results))*100,4), "total_trades": tr, "total_wins": w, "total_losses": l, "win_rate_pct": round(w/tr*100,2) if tr else 0, "gross_profit": round(gp,6), "gross_loss": round(gl,6), "profit_factor": round(gp/abs(gl),4) if gl else None, "sum_symbol_max_dd": round(dd,6)}


def main():
    start=parse_dt(os.environ.get("BACKTEST_START","2026-01-01T00:00:00+00:00")); end=parse_dt(os.environ.get("BACKTEST_END","2026-06-01T00:00:00+00:00"))
    candles={}
    for s in SYMBOLS:
        print(f"Fetching {s} {start.isoformat()} -> {end.isoformat()}...", flush=True)
        candles[s]=fetch_klines(s,start,end); print(f"{s}: {len(candles[s])} candles", flush=True)
    variants=[]
    for tol in [0.0005,0.001,0.0015,0.002]:
        for tp in [0.0025,0.0035,0.005,0.0075,0.01,0.015]:
            for filt,name in [(dict(),"base"),(dict(require_candle_direction=True),"dir"),(dict(require_momentum_1h=True),"mom"),(dict(a_plus_only=True),"aplus"),(dict(a_plus_only=True,require_candle_direction=True),"aplus_dir")]:
                variants.append(Variant(name=f"fixed_{name}_tol{tol}_tp{tp}", retest_tolerance_pct=tol, fixed_tp_pct=tp, **filt))
    runs=[]
    for v in variants:
        symres={s:summarize(s,c,v) for s,c in candles.items()}
        runs.append({"variant":v.__dict__,"combined":combine(symres),"symbols":symres})
    candidates=[r for r in runs if r["combined"]["total_trades"]>=80 and r["combined"]["total_net_pnl"]>0]
    top_balanced=sorted(candidates,key=lambda r:(r["combined"]["win_rate_pct"]>=45, r["combined"]["total_net_pnl"], r["combined"]["win_rate_pct"]),reverse=True)[:20]
    top_win=sorted(candidates,key=lambda r:(r["combined"]["win_rate_pct"],r["combined"]["total_net_pnl"]),reverse=True)[:20]
    top_pnl=sorted(candidates,key=lambda r:(r["combined"]["total_net_pnl"],r["combined"]["win_rate_pct"]),reverse=True)[:20]
    report={"generated_at":datetime.now(timezone.utc).isoformat(),"period":{"start":start.isoformat(),"end":end.isoformat()},"assumptions":{"initial_equity_per_symbol":INITIAL_EQUITY,"leverage":LEVERAGE,"fees_slippage_funding":"not included","exit":"full fixed take profit or VWAP stop"},"top_balanced":top_balanced,"top_winrate":top_win,"top_pnl":top_pnl}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print("TOP BALANCED")
    for r in top_balanced[:10]:
        c=r["combined"]; print(r["variant"]["name"],"pnl",c["total_net_pnl"],"ret",c["total_return_pct"],"win",c["win_rate_pct"],"trades",c["total_trades"],"pf",c["profit_factor"],"ddsum",c["sum_symbol_max_dd"])
    print(f"Saved {OUT}")

if __name__ == "__main__": main()
