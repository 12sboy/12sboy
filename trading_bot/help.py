from __future__ import annotations


def command_help() -> str:
    return """매매봇 명령어 도움말

로컬/Termux 실행:
  cd /data/data/com.termux/files/home/work/bot

주요 명령어:
  python3 -m trading_bot help --config config.yaml
    이 도움말을 출력합니다.

  python3 -m trading_bot report --config config.yaml
    현재 BTC/ETH 시장상황, 잔고, 포지션 요약 리포트를 출력하고 텔레그램 설정이 있으면 알림으로 보냅니다.

  python3 -m trading_bot daily --config config.yaml
    오늘 실현 손익, 승률, 손실합계, 롱/숏 거래 수를 출력합니다.

  python3 -m trading_bot weekly --config config.yaml
    이번 주 실현 손익, 승률, 손실합계, 롱/숏 거래 수를 출력합니다.

  python3 -m trading_bot once --config config.yaml
    최신 5분봉 기준으로 BTC/ETH를 한 번 평가합니다.
    mode: demo 상태에서는 조건이 맞으면 Bybit Demo Trading 주문이 생성될 수 있습니다.

  python3 -m trading_bot run --config config.yaml
    봇을 계속 실행합니다. loop_interval_sec 주기마다 평가하고, hourly_report가 true면 1시간마다 리포트를 보냅니다.

  python3 -m trading_bot telegram-poll --config config.yaml
    텔레그램에서 들어온 /help, /status 명령을 한 번 처리합니다. 매매 판단/주문은 하지 않습니다.

  python3 -m trading_bot backtest --config config.yaml --output reports/backtest.json
    기본 샘플 백테스트를 실행하고 결과를 저장합니다.

기간 백테스트:
  INITIAL_EQUITY_PER_SYMBOL=1000 LEVERAGE=5 \
  BACKTEST_START=2026-05-01T00:00:00+00:00 \
  BACKTEST_END=2026-06-01T00:00:00+00:00 \
  BACKTEST_OUT=reports/backtest_custom.json \
  python3 scripts/backtest_period.py

현재 핵심 설정:
  mode: demo
  symbols: BTC/USDT, ETH/USDT
  leverage: 5x
  entry: Limit
  exit: Market reduceOnly
  take profit: fixed_take_profit_pct 0.25%
  stop loss: VWAP 이탈 즉시 손절

주의:
  현재 mode가 demo라서 once/run은 Bybit Demo Trading 주문을 낼 수 있습니다.
  실계좌가 아니라 데모지만, 주문 테스트 전에는 report로 연결 상태를 먼저 확인하세요.
"""


def telegram_help() -> str:
    return """매매봇 도움말

텔레그램 명령어:
/help - 이 도움말 보기
/status - 현재 BTC/ETH 시장상황, 잔고, 포지션 리포트 받기
/daily - 오늘 일간 손익 리포트 받기
/weekly - 이번 주 손익 리포트 받기

현재 봇 설정:
- Bybit Demo Trading 모드
- BTC/USDT, ETH/USDT
- 5분봉
- 5배 레버리지
- 고정익절 0.25%
- 손절: VWAP 이탈 즉시 손절

자동매매 실행/중지는 Termux에서 관리합니다.

Termux 실행 명령어:
cd /data/data/com.termux/files/home/work/bot
python3 -m trading_bot run --config config.yaml

주의: 텔레그램에서는 안전을 위해 주문 실행 명령을 받지 않습니다. /status, /daily, /weekly, /help만 처리합니다.
"""


TELEGRAM_BOT_COMMANDS = [
    {"command": "help", "description": "매매봇 도움말 보기"},
    {"command": "status", "description": "시장상황/잔고/포지션 리포트"},
    {"command": "daily", "description": "오늘 일간 손익 리포트"},
    {"command": "weekly", "description": "이번 주 손익 리포트"},
]
