from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_2y import SYMBOLS, fetch_klines
from scripts.evaluate_strategy_variants import combine, parse_dt, run_symbol
from trading_bot.bot import load_config

OUT = Path("reports/current_bot_review_strategy_fix.json")
PERIODS = [
    ("post_change_recent", "2026-06-05T08:00:00+00:00", "2026-06-09T01:49:00+00:00"),
    ("may_to_now", "2026-05-01T00:00:00+00:00", "2026-06-09T01:49:00+00:00"),
    ("year_to_now", "2026-01-01T00:00:00+00:00", "2026-06-09T01:49:00+00:00"),
]


def main():
    cfg = load_config("config.yaml")
    variants = {
        "selected_fresh_setup_ema_align": cfg.strategy,
        "fresh_setup_no_ema_align": replace(cfg.strategy, require_ema_vwap_alignment=False),
        "momentum_filter": replace(cfg.strategy, require_momentum_1h=True),
        "shorter_setup_age_12": replace(cfg.strategy, setup_max_age_bars=12),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"strategy": asdict(cfg.strategy), "risk": asdict(cfg.risk)},
        "notes": [
            "selected variant consumes each breakout/breakdown setup once to avoid repeated live entries from the same stale setup",
            "fees are modeled in evaluate_strategy_variants; funding/slippage are not modeled",
        ],
        "periods": {},
    }
    for label, start, end in PERIODS:
        print(label, start, end, flush=True)
        candles = {s: fetch_klines(s, parse_dt(start), parse_dt(end)) for s in SYMBOLS}
        period = {"start": start, "end": end, "candle_counts": {s: len(v) for s, v in candles.items()}, "variants": {}}
        for name, strategy in variants.items():
            symres = {s: run_symbol(s, candles[s], strategy) for s in SYMBOLS}
            period["variants"][name] = {"combined": combine(symres), "symbols": symres}
            print(name, period["variants"][name]["combined"], flush=True)
        report["periods"][label] = period
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved {OUT}")

if __name__ == "__main__":
    main()
