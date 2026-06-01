#!/data/data/com.termux/files/usr/bin/bash
set -u
PROJECT_DIR="/data/data/com.termux/files/home/work/bot"
cd "$PROJECT_DIR"
mkdir -p run logs
STOP_FILE="run/STOP_TRADING_BOT"
SUPERVISOR_PID_FILE="run/trading_bot_supervisor.pid"
BOT_PID_FILE="run/trading_bot.pid"

touch "$STOP_FILE"

if [ -f "$BOT_PID_FILE" ]; then
  bot_pid=$(cat "$BOT_PID_FILE" 2>/dev/null || true)
  if [ -n "${bot_pid:-}" ] && kill -0 "$bot_pid" 2>/dev/null; then
    kill "$bot_pid" 2>/dev/null || true
  fi
fi

if [ -f "$SUPERVISOR_PID_FILE" ]; then
  sup_pid=$(cat "$SUPERVISOR_PID_FILE" 2>/dev/null || true)
  if [ -n "${sup_pid:-}" ] && kill -0 "$sup_pid" 2>/dev/null; then
    kill "$sup_pid" 2>/dev/null || true
  fi
fi

if command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock || true
fi

echo "stop requested"
