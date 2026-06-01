# PBInvesting-style Bybit BTC/ETH Bot

비트코인과 이더리움 5분봉 기준의 PBInvesting식 VWAP + 8 EMA 단순 규칙 봇입니다.

## 기본 원칙

- 기본 모드는 `paper`라서 실제 주문을 내지 않습니다.
- Bybit Demo Trading 주문 모드(`mode: demo`)를 구현했습니다.
- 대상 심볼: `BTC/USDT`, `ETH/USDT`
- 거래소 데이터: Bybit v5 kline API
- 주문 방식: 기본 `Limit` 주문. 청산은 `Market` reduce-only 주문.
- 선물 레버리지: 기본 `5x` (`config.yaml`의 `risk.leverage`).
- 텔레그램 알림: 주문/체결 이벤트, 상태 리포트, 오류 알림.

## 실행

```bash
cd /data/data/com.termux/files/home/work/bot
python3 -m trading_bot help --config config.yaml
python3 -m trading_bot report --config config.yaml
python3 -m trading_bot once --config config.yaml
python3 -m trading_bot telegram-poll --config config.yaml
python3 -m trading_bot backtest --config config.yaml --output reports/backtest.json
python3 -m unittest discover -s tests -v
```

계속 구동:

```bash
python3 -m trading_bot run --config config.yaml
```

## Bybit 데모트레이딩 연결

1. 환경변수 파일 생성:

```bash
cp .env.example .env
```

2. `.env`에 값 입력:

```bash
BYBIT_API_KEY=발급받은키
BYBIT_API_SECRET=발급받은시크릿
TELEGRAM_BOT_TOKEN=텔레그램봇토큰
TELEGRAM_CHAT_ID=알림받을채팅ID
```

3. 실행하면 `.env`는 자동으로 로드됩니다. `config.yaml`에서 변경:

```yaml
mode: demo
```

4. 먼저 리포트/1회 평가로 연결 확인:

```bash
python3 -m trading_bot report --config config.yaml
python3 -m trading_bot once --config config.yaml
```

5. 문제 없으면 지속 실행:

```bash
python3 -m trading_bot run --config config.yaml
```

## 전략 구현

롱:
1. 5분봉 종가가 `pre_high` 위로 돌파 마감
2. 이후 가격이 VWAP 근처로 눌림/리테스트
3. 종가가 VWAP 이상이고 양봉일 때만 `BUY`
4. VWAP 아래 종가 마감 시 손절
5. 진입가 대비 `fixed_take_profit_pct` 만큼 상승하면 전량 익절
6. `fixed_take_profit_pct: null`로 끄면 기존 EMA8 50% 익절/트레일링 모드 사용

숏:
1. 5분봉 종가가 `pre_low` 아래로 이탈 마감
2. 이후 가격이 VWAP 근처로 반등/리테스트
3. 종가가 VWAP 이하이고 음봉일 때만 `SELL`
4. VWAP 위 종가 마감 시 손절
5. 진입가 대비 `fixed_take_profit_pct` 만큼 하락하면 전량 익절
6. `fixed_take_profit_pct: null`로 끄면 기존 EMA8 50% 익절/트레일링 모드 사용

A+ 셋업:
- VWAP가 pre_high/pre_low/prior_high/prior_low 중 하나와 설정 오차 이내로 정렬되면 포지션 배수를 높입니다.

## 1시간 시장 리포트

`run` 모드에서 매 시간 1회 텔레그램으로 전송합니다.

포함 항목:
- BTC/ETH 시장상황: 상승/하락/횡보
- 현재가
- 최근 1시간 변화율
- VWAP / EMA8
- USDT 잔고/equity
- 현재 봇 내부 포지션 상태

수동 실행:

```bash
python3 -m trading_bot report --config config.yaml
```

## 독립형 텔레그램 모니터링 모듈

요구사항용 전체 모듈은 `trading_bot/telegram_monitor.py`에 있습니다. 기존 자동매매 엔진과 분리해서 사용할 수 있는 이벤트 콜백형 모듈입니다.

### 제공 기능

- 진입 알림: 방향, 진입가, 물량, 레버리지, 초 단위 시간, 주문ID
- 청산 알림: 방향, 진입가/청산가, 실현손익 USDT/% 기준, 손절 여부
- 손절 특별 경고: `⚠️ 손절 실행!` 및 손실 금액 강조
- 시간 리포트: 1시간 OHLC, 추세, 잔고, 미실현 손익, 보유 포지션, 해당 시간 실현손익
- 일간 리포트: 당일 PnL, 최대 손실폭, 승률, 롱/숏 거래 수, 손절 손실 총합
- 주간 리포트: 주간 PnL, 수익률, 최대 자본 인하율, 통계, 다음 주 관심 지표
- 명령어: `/status`, `/daily`, `/weekly`, `/help`
- 텔레그램 API rate limit 보호: 큐 기반 전송 + 최대 초당 30개 이하 제한
- 1초 내 동일 타입 알림 병합 옵션
- 예외 발생 시 텔레그램 오류 알림

### 설치

```bash
pip install -r requirements.txt
```

`python-telegram-bot>=20`이 필요합니다. 단, 로컬 시뮬레이션과 단위 테스트는 콘솔 클라이언트를 사용하므로 실제 텔레그램 토큰 없이도 실행됩니다.

### 환경 변수

```bash
TELEGRAM_BOT_TOKEN=123456:ABCDEF
TELEGRAM_CHAT_ID=123456789
TELEGRAM_PARSE_MODE=HTML
TELEGRAM_RATE_LIMIT_PER_SEC=25
TELEGRAM_MERGE_WINDOW_SEC=1
```

### 기존 자동매매봇 통합 예시

```python
from trading_bot.telegram_monitor import (
    TelegramMonitoringModule,
    TradeEntryEvent,
    TradeExitEvent,
)

monitor = TelegramMonitoringModule(provider=my_report_provider)
await monitor.start()

# 진입 체결/주문 콜백에서 호출
await monitor.notify_entry(TradeEntryEvent(
    symbol="BTC/USDT",
    direction="LONG",
    entry_price=65200,
    quantity=0.05,
    quantity_unit="BTC",
    leverage=10,
    order_id="exchange-order-id",
))

# 청산 체결 콜백에서 호출
await monitor.notify_exit(TradeExitEvent(
    symbol="BTC/USDT",
    direction="LONG",
    entry_price=65200,
    exit_price=66100,
    quantity=0.05,
    realized_pnl_usdt=450,
    realized_pnl_pct=6.9,
    is_stop_loss=False,
))
```

`/status`, `/daily`, `/weekly`, 시간/일간/주간 리포트를 쓰려면 `ReportDataProvider` 프로토콜을 구현해야 합니다.

```python
class MyReportProvider:
    async def account_snapshot(self): ...
    async def market_snapshots_1h(self): ...
    async def realized_pnl_since(self, since): ...
    async def daily_summary(self, day=None): ...
    async def weekly_summary(self, week_start=None): ...
```

명령어 봇을 별도 프로세스로 띄우려면:

```python
app = monitor.build_application()
app.run_polling()
```

자동매매 루프 내부에서 스케줄러를 쓸 경우:

```python
asyncio.create_task(monitor.run_schedulers())
```

APScheduler를 이미 쓰고 있다면 `send_hourly_report`, `send_daily_report`, `send_weekly_report`를 원하는 cron에 등록하면 됩니다.

### 로컬 테스트/가상 거래 시뮬레이션

```bash
python3 scripts/simulate_telegram_monitor.py
cat reports/telegram_monitor_demo.txt
```

이 스크립트는 실제 텔레그램으로 보내지 않고 콘솔/파일에 메시지를 출력합니다.

## 텔레그램 명령어

`run` 모드로 봇이 실행 중이면 텔레그램 채팅에서 아래 명령어를 사용할 수 있습니다.

- `/help`: 매매봇 도움말 보기
- `/status`: 현재 BTC/ETH 시장상황, 잔고, 포지션 리포트 받기

안전을 위해 텔레그램에서는 주문 실행 명령을 받지 않습니다. 자동매매 시작/중지는 Termux에서 `run` 프로세스로 관리합니다.

텔레그램 명령을 한 번만 수동 처리하려면:

```bash
python3 -m trading_bot telegram-poll --config config.yaml
```

## 설정 파일

`config.yaml`에서 리스크와 전략 민감도를 바꿀 수 있습니다.

- `mode`: `paper`, `demo`, `testnet`, `public`, `csv`
- `order_type`: 진입 주문 타입. 기본 `Limit`
- `loop_interval_sec`: 루프 주기
- `hourly_report`: 1시간 리포트 사용 여부
- `retest_tolerance_pct`: VWAP 리테스트 허용 오차
- `a_plus_alignment_pct`: A+ 가격 정렬 허용 오차
- `fixed_take_profit_pct`: 고승률 스캘핑형 고정 전량 익절 기준. 현재 기본값 `0.0025` = 0.25%
- `require_candle_direction`: 롱=양봉, 숏=음봉일 때만 진입. 현재 기본값 `true`
- `ema_space_take_profit_pct`: `fixed_take_profit_pct`를 끈 경우 사용하는 EMA8 이격 1차 익절 기준
- `risk_per_trade_pct`: 1회 거래 리스크
- `max_notional_pct`: 계좌 대비 최대 명목 포지션
- `leverage`: Bybit 선물 레버리지. 현재 기본값 `5`

## 주의

- 데모트레이딩이라도 실제 주문 API를 호출합니다. 반드시 `mode: paper`에서 먼저 테스트하세요.
- API 키, 토큰 등 민감 정보는 코드에 하드코딩하지 말고 `.env` 또는 배포 환경 변수로만 주입하세요.
- 텔레그램 채널에 보내려면 봇을 채널 관리자로 추가하고 `TELEGRAM_CHAT_ID`를 채널 ID로 설정하세요.
- Bybit 계정 포지션 모드, 레버리지, 최소 주문 수량/호가 단위는 계정 설정에 따라 주문 거절이 발생할 수 있습니다.
- 현재 봇은 프로세스 메모리에 포지션을 저장합니다. 장기 운영 전에는 DB/파일 상태 저장 기능을 추가하는 것이 좋습니다.
