#!/data/data/com.termux/files/usr/bin/bash
set -u

PROJECT_DIR="/data/data/com.termux/files/home/work/bot"
cd "$PROJECT_DIR"

mkdir -p logs reports run
SUPERVISOR_PID_FILE="run/trading_bot_supervisor.pid"
BOT_PID_FILE="run/trading_bot.pid"
LOG_FILE="logs/trading_bot_supervisor.log"
BOT_LOG_FILE="logs/trading_bot_run.log"
STOP_FILE="run/STOP_TRADING_BOT"

# Keep Android from sleeping the process when Termux:API is available.
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

printf '%s\n' "$$" > "$SUPERVISOR_PID_FILE"
rm -f "$STOP_FILE"

notify() {
  local msg="$1"
  python3 - <<'PY' "$msg" >/dev/null 2>&1 || true
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path('/data/data/com.termux/files/home/work/bot')))
from trading_bot.env import load_dotenv
from trading_bot.notifier import TelegramNotifier
load_dotenv('/data/data/com.termux/files/home/work/bot/.env')
token = os.environ.get('TELEGRAM_BOT_TOKEN')
chat_id = os.environ.get('TELEGRAM_CHAT_ID')
if token and chat_id:
    TelegramNotifier(token, chat_id).send(sys.argv[1])
PY
}

notify "매매봇 supervisor 시작: 자동 재시작 감시를 시작합니다."

echo "[$(date -Is)] supervisor started pid=$$" >> "$LOG_FILE"

while true; do
  if [ -f "$STOP_FILE" ]; then
    echo "[$(date -Is)] stop file detected, supervisor exiting" >> "$LOG_FILE"
    notify "매매봇 supervisor 중지: STOP 파일이 감지되어 종료합니다."
    rm -f "$SUPERVISOR_PID_FILE" "$BOT_PID_FILE"
    exit 0
  fi

  echo "[$(date -Is)] starting trading bot" >> "$LOG_FILE"
  notify "매매봇 실행 시작: Bybit Demo Trading 자동매매 루프를 시작합니다."

  python3 -m trading_bot run --config config.yaml >> "$BOT_LOG_FILE" 2>&1 &
  bot_pid=$!
  printf '%s\n' "$bot_pid" > "$BOT_PID_FILE"
  wait "$bot_pid"
  exit_code=$?

  echo "[$(date -Is)] trading bot exited pid=$bot_pid exit_code=$exit_code" >> "$LOG_FILE"
  notify "매매봇 재시작 예정: 프로세스가 종료되었습니다. exit_code=$exit_code, 10초 후 재시작합니다."

  sleep 10
done
