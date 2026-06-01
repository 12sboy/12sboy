from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot.telegram_monitor import run_demo_simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate Telegram monitoring alerts without sending real Telegram messages")
    parser.add_argument("--output", default="reports/telegram_monitor_demo.txt", help="file to save rendered messages")
    args = parser.parse_args()
    messages = asyncio.run(run_demo_simulation())
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n---\n\n".join(messages), encoding="utf-8")
    print(f"saved {len(messages)} messages to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
