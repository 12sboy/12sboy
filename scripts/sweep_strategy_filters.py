from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_2y import fetch_klines, SYMBOLS
from scripts.evaluate_strategy_variants import run_symbol, combine, parse_dt
from trading_bot.core import StrategyConfig

INITIAL_EQUITY = float(os.environ.get("INITIAL_EQUITY_PER_SYMBOL", "1000"))
OUT = Path(os.environ.get("SWEEP_OUT", "reports/strategy_filter_sweep.json"))

def main():
    start = parse_dt(os.environ.get("BACKTEST_START", "2026-05-01T00:00:00+00:00"))
    end = parse_dt(os.environ.get("BACKTEST_END", "2026-06-05T06:00:00+00:00"))
    candles = {}
    for s in SYMBOLS:
        print(f"Fetching {s} {start.isoformat()} -> {end.isoformat()}...", flush=True)
        candles[s] = fetch_klines(s, start, end)
        print(f"{s}: {len(candles[s])} candles", flush=True)
    variants = []
    for tp in [0.0025, 0.0035, 0.005, 0.0075]:
      for age in [6, 12, 24, 48]:
        for mom in [False, True]:
          for align in [False, True]:
            for dist in [0.0, 0.0015, 0.003, 0.005]:
              variants.append(StrategyConfig(
                retest_tolerance_pct=0.002,
                fixed_take_profit_pct=tp,
                require_candle_direction=True,
                require_momentum_1h=mom,
                require_ema_vwap_alignment=align,
                setup_max_age_bars=age,
                min_breakout_pct=0.0005,
                max_close_vwap_distance_pct=dist,
                trade_start_hour_utc=8,
              ))
    runs=[]
    for cfg in variants:
        symres={s:run_symbol(s, rows, cfg) for s,rows in candles.items()}
        comb=combine(symres)
        runs.append({"config": asdict(cfg), "combined": comb, "symbols": symres})
    good=[r for r in runs if r["combined"]["total_trades"]>=8]
    top=sorted(good, key=lambda r:(r["combined"]["total_net_pnl"], r["combined"]["win_rate_pct"], r["combined"]["profit_factor"] or 0), reverse=True)[:25]
    report={"generated_at":datetime.now(timezone.utc).isoformat(),"period":{"start":start.isoformat(),"end":end.isoformat()},"top":top}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    for r in top[:15]: print(r["config"], r["combined"])
    print(f"Saved {OUT}")
if __name__ == "__main__": main()
