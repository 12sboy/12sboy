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
- Bybit 계정 포지션 모드, 레버리지, 최소 주문 수량/호가 단위는 계정 설정에 따라 주문 거절이 발생할 수 있습니다.
- 현재 봇은 프로세스 메모리에 포지션을 저장합니다. 장기 운영 전에는 DB/파일 상태 저장 기능을 추가하는 것이 좋습니다.
