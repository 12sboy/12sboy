from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bot import TradingBot, load_config, run_backtest_from_public
from .env import load_dotenv
from .help import command_help


def main() -> int:
    parser = argparse.ArgumentParser(description="PBInvesting-style Bybit BTC/ETH trading bot")
    parser.add_argument("command", choices=["help", "once", "backtest", "report", "telegram-poll", "run"], help="help: show commands; once: evaluate latest candles; backtest: public Bybit historical sample; report: send/print hourly report; telegram-poll: process Telegram commands once; run: continuous loop")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    if args.command == "help":
        result = command_help()
    elif args.command == "once":
        result = TradingBot(config).run_once()
    elif args.command == "backtest":
        result = run_backtest_from_public(config)
    elif args.command == "report":
        result = TradingBot(config).build_hourly_report()
    elif args.command == "telegram-poll":
        bot = TradingBot(config)
        bot.setup_telegram_commands()
        result = {"handled": bot.process_telegram_commands_once()}
    else:
        TradingBot(config).run_forever()
        return 0
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
