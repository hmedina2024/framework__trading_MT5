# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

**Start the FastAPI server (main entry point):**
```powershell
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

**Run the legacy CLI trading app (standalone, no API):**
```powershell
python trading_app.py
```

**Install dependencies:**
```powershell
pip install -r requirements.txt
```

No test suite exists yet — manual testing is done against a live MT5 demo account via the API at `http://localhost:8000/docs`.

## Architecture Overview

This is a **FastAPI-based automated trading platform** that connects to MetaTrader 5 and manages multiple trading bots concurrently. The system has two layers:

### 1. Framework Layer (`core/`, `platform_connector/`, `models/`, `utils/`)
Low-level building blocks used by strategies and the API:
- `platform_connector/` — wraps the `MetaTrader5` Python library; all MT5 calls go through here
- `core/order_manager.py` — open/close/modify positions
- `core/risk_manager.py` — validates trades, calculates position sizes, enforces risk limits
- `core/market_analyzer.py` — technical indicators (RSI, MACD, Bollinger, ATR, ADX, EMA)
- `models/trade_models.py` — Pydantic models (`TradeRequest`, `TradeResult`, `Position`, etc.)

### 2. Application Layer (`api/`, `strategies/`)
- `api/main.py` — FastAPI app with lifespan; on startup initializes `TradingService`, loads `bots_config.json` to auto-restart previously active bots, and starts the `RegimeDetector` scheduler
- `api/core/trading_service.py` — **singleton** that owns all active strategies, handles bot start/stop, runs backtests, and persists active bots to `bots_config.json`
- `api/routers/` — REST endpoints: `account`, `market`, `orders`, `strategies`, `analysis`
- `strategies/strategy_base.py` — abstract base class all strategies inherit; contains all protective logic
- `strategies/*.py` — concrete strategy implementations (MA_CROSS, RSI, BOLLINGER, MACD, BREAKOUT, SUPERTREND, EMA_CROSS, WILLIAMS_R, LONDON_ORB)

### Regime-Adaptive Bot Management
`core/regime_detector.py` runs on a background thread, re-evaluating market regime every 4 hours and at 07:00 UTC (London open). Based on ADX strength it classifies the market into 5 regimes (`RANGING_PURE`, `RANGING_MILD`, `TRENDING_MILD`, `TRENDING_STRONG`, `TRENDING_EXTREME`, `VOLATILE`) and automatically starts/stops the appropriate strategy bots for each symbol (max 2 bots per symbol).

### Strategy Execution Loop
Each strategy runs in its own daemon thread (started by `StrategyBase.start()`), calling `run_iteration()` every 60 seconds. Each iteration: fetches 200 candles → calls `analyze()` → if signal, calls `execute_signal()` with all safety checks → manages open positions (trailing stop, exit conditions).

### Safety Layers in `execute_signal()` (in order)
1. **Circuit Breaker** (`core/circuit_breaker.py`) — blocks a bot if accumulated losses exceed `MAX_LOSS_USD` ($50 default); OPEN→HALF_OPEN auto-recovery after 24h
2. Session filter — no entries outside 07:00–20:00 UTC
3. Correlation filter — blocks duplicate USD exposure across correlated pairs
4. News filter — blocks entries 30 min before/after high-impact ForexFactory events
5. Cooldown — 30 min post-trade per symbol
6. Daily trade limit — 10 trades/symbol/day
7. Risk manager validation
8. Minimum R:R ratio (1.0)

### Persistence Files (project root)
- `bots_config.json` — list of active bots, auto-written on every start/stop; read on server startup for auto-restart
- `stats_<STRATEGY_ID>.json` — per-bot trade stats (wins/losses/count) that survive server restarts; used for auto-scaling risk
- `circuit_breaker_state.json` — circuit breaker state per bot

## Adding a New Strategy

1. Create `strategies/my_strategy.py` inheriting `StrategyBase`
2. Implement `analyze(symbol, df) -> Optional[Dict]` returning `{'direction': 'BUY'|'SELL', ...}` or `None`
3. Implement `calculate_entry_exit(symbol, signal) -> Dict` returning `{'entry': ..., 'stop_loss': ..., 'take_profit': ...}`
4. Register in `api/core/trading_service.py`: add to `STRATEGY_CATALOG`, `STRATEGY_MAGIC_BASE`, `start_strategy()` dispatch, and the backtest `strategy_map`
5. Export from `strategies/__init__.py`

Magic numbers are deterministic: `STRATEGY_MAGIC_BASE[type] + SYMBOL_OFFSET[symbol]`. Each strategy type has its own base (210000–280000+) so MT5 trade history can be attributed even if the comment field is overwritten.

## Environment Variables (`.env`)

```
MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH  — MT5 connection
MAX_RISK_PER_TRADE=0.02                         — 2% per trade
MAX_DAILY_LOSS=0.05                             — 5% daily loss cap
MAX_OPEN_POSITIONS=5                            — concurrent positions
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID           — optional alerts
```

Telegram notifier is optional — `ImportError` is silently caught and notifications are skipped.

## Key Design Decisions

- `TradingService` and `CircuitBreaker` are **singletons** — access the live state via the module-level `circuit_breaker = CircuitBreaker()` instance
- All blocking MT5 calls run via `loop.run_in_executor(None, ...)` from async API handlers to avoid blocking the event loop
- Strategy threads are **daemon threads** — they die with the main process; `stop()` sets `is_running = False` and waits for the loop to notice
- `_strategy_id` is assigned by `TradingService.start_strategy()` after construction (format: `"TYPE_SYMBOL"`) and must match across `active_strategies`, `bots_config.json`, and `stats_*.json`
