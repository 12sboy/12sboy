from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_2y import fetch_klines, run_backtest, combine, SYMBOLS, INITIAL_EQUITY, TIMEFRAME, RISK, STRATEGY


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def main() -> None:
    start = parse_dt(os.environ.get('BACKTEST_START', '2026-05-01T00:00:00+00:00'))
    end = parse_dt(os.environ.get('BACKTEST_END', '2026-06-01T00:00:00+00:00'))
    out = Path(os.environ.get('BACKTEST_OUT', 'reports/backtest_may_2026_total_2000_report.json'))
    results = {}
    for symbol in SYMBOLS:
        print(f'Fetching {symbol} {start.isoformat()} -> {end.isoformat()}...', flush=True)
        candles = fetch_klines(symbol, start, end)
        print(f'Backtesting {symbol}: {len(candles)} candles', flush=True)
        results[symbol] = run_backtest(symbol, candles)
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'requested_period': {'start': start.isoformat(), 'end': end.isoformat()},
        'assumptions': {
            'initial_equity_per_symbol': INITIAL_EQUITY,
            'total_initial_equity': INITIAL_EQUITY * len(SYMBOLS),
            'timeframe': TIMEFRAME,
            'fees_slippage': 'not included',
            'position_sizing': {'risk_per_trade_pct': RISK.risk_per_trade_pct, 'max_notional_pct': RISK.max_notional_pct, 'leverage': RISK.leverage},
            'strategy': {
                'retest_tolerance_pct': STRATEGY.retest_tolerance_pct,
                'fixed_take_profit_pct': STRATEGY.fixed_take_profit_pct,
                'require_candle_direction': STRATEGY.require_candle_direction,
                'stop_loss': 'VWAP close break',
            },
        },
        'combined': combine(results),
        'symbols': results,
    }
    report['combined']['initial_equity_note'] = f'총 자본 {INITIAL_EQUITY * len(SYMBOLS):g} USDT를 BTC/USDT {INITIAL_EQUITY:g}, ETH/USDT {INITIAL_EQUITY:g}로 균등 배분한 독립 백테스트. combined는 단순 합산.'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report['combined'], ensure_ascii=False, indent=2))
    print(f'Saved {out}')


if __name__ == '__main__':
    main()
