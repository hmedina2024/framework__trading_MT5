from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
import MetaTrader5 as mt5
from api.core.trading_service import TradingService

router = APIRouter()

# Estado del comparador de estrategias — vive en memoria del proceso, un solo
# barrido a la vez. Se persiste a disco al terminar para consultarlo tras un
# reinicio (el backtest en sí no sobrevive un reload/restart de todas formas).
_COMPARISON_STATE = {
    "running":       False,
    "started_at":    None,
    "finished_at":   None,
    "days":          None,
    "total_combos":  0,
    "completed":     0,
    "results":       [],
    "errors":        [],
}
_COMPARISON_RESULTS_FILE = "strategy_comparison_results.json"

# Timeframe por símbolo (coincide con SYMBOL_CONFIG de regime_detector)
_BACKFILL_TF = {
    'US30':   mt5.TIMEFRAME_H4,
    'BTCUSD': mt5.TIMEFRAME_H4,
}
# Reverse map de la base del magic number → tipo de estrategia
_MAGIC_BASE_TO_STRATEGY = {
    210000: 'MA_CROSS',  220000: 'RSI',        230000: 'BOLLINGER',
    240000: 'MACD',      250000: 'BREAKOUT',   260000: 'SUPERTREND',
    270000: 'EMA_CROSS', 280000: 'WILLIAMS_R', 300000: 'LONDON_ORB',
    310000: 'FVG',       320000: 'NY_ORB',
}
# El servidor del bróker corre en UTC+2 — restamos para aproximar la hora UTC real
_SERVER_UTC_OFFSET_H = 2

def get_trading_service():
    from api.main import trading_service
    return trading_service

@router.get("/full/{symbol}")
async def get_full_analysis(symbol: str, service: TradingService = Depends(get_trading_service)):
    """
    Endpoint dedicado para el dashboard de análisis.
    Devuelve indicadores, tendencia y señales en una sola llamada.
    """
    analysis = service.get_market_analysis(symbol)
    if not analysis:
        raise HTTPException(status_code=404, detail="No se pudo analizar el mercado")
        
    # Enriquecer respuesta para el frontend
    return {
        "symbol": symbol,
        "price": analysis["current_price"],
        "trend_direction": analysis["trend"], # UPTREND, DOWNTREND, SIDEWAYS
        "signals": analysis["signals"], # BUY, SELL, NEUTRAL
        "indicators": analysis["indicators"],
        "support_resistance": analysis["levels"]
    }


@router.get("/ml-status")
async def get_ml_status():
    """
    Estado del filtro de señales con ML (SignalFilter).
    Permite monitorear desde /docs o el frontend si el modelo LightGBM ya se
    activó, cuántas muestras etiquetadas tiene, cuántas faltan para entrenar,
    el win rate de las muestras y la importancia de cada feature.
    """
    try:
        from core.signal_filter import signal_filter
        return signal_filter.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado ML: {e}")


def _reconstruct_context(connector, market_analyzer, trade, win_rate):
    """
    Reconstruye el dict de features de un trade histórico, replicando la forma
    de StrategyBase._build_signal_context. Las features de mercado (ADX, ATR,
    Bollinger) se recalculan sobre las velas que existían en la hora de entrada;
    spread_ratio se aproxima (no hay histórico) y win_rate se pasa ya calculado.
    """
    symbol     = trade['symbol']
    entry_time = trade['entry_time']
    tf         = _BACKFILL_TF.get(symbol, mt5.TIMEFRAME_H1)

    adx, atr_ratio, bb_ratio = 25.0, 1.0, 1.0

    # Velas terminadas en la hora de entrada (se descarta la última = vela de
    # entrada, para usar la vela cerrada sobre la que disparó la señal)
    try:
        df = connector.get_historical_data(symbol, tf, entry_time, count=62)
        if df is not None and len(df) > 21:
            df = df.iloc[:-1]
            adx_val = market_analyzer.calculate_adx(df, period=14)
            if adx_val:
                adx = float(adx_val)
            try:
                upper, middle, lower = market_analyzer.calculate_bollinger_bands(df, 20, 2.0)
                width = (upper - lower) / middle * 100
                avg_w = width.iloc[-20:].mean()
                if avg_w > 0:
                    bb_ratio = float(width.iloc[-1] / avg_w)
            except Exception:
                pass
    except Exception:
        pass

    # ATR ratio con velas diarias de esa fecha
    try:
        df_d = connector.get_historical_data(symbol, mt5.TIMEFRAME_D1, entry_time, count=22)
        if df_d is not None and len(df_d) >= 6:
            df_d = df_d.iloc[:-1]
            atr_s = market_analyzer.calculate_atr(df_d, period=14)
            avg = atr_s.iloc[-20:-1].mean()
            if avg > 0:
                atr_ratio = float(atr_s.iloc[-1] / avg)
    except Exception:
        pass

    entry_utc = entry_time - timedelta(hours=_SERVER_UTC_OFFSET_H)
    return {
        'adx':            adx,
        'atr_ratio':      atr_ratio,
        'hour_utc':       entry_utc.hour,
        'day_of_week':    entry_utc.weekday(),
        'spread_ratio':   0.5,                 # aproximado: sin histórico de spread
        'win_rate':       win_rate,
        'direction':      trade['direction'],
        'bb_width_ratio': bb_ratio,
    }


@router.post("/ml-backfill")
async def ml_backfill(days: int = 120, service: TradingService = Depends(get_trading_service)):
    """
    Bootstrap del modelo ML con el historial real de MT5.
    Reconstruye muestras etiquetadas de los trades cerrados de los últimos
    `days` días (solo los de los bots, identificados por magic number) y las
    importa al SignalFilter marcadas como 'mt5_backfill'. Si se alcanzan 50
    muestras, entrena LightGBM de inmediato.

    Las muestras históricas se purgan automáticamente cuando se acumulen 50
    muestras EN VIVO (más precisas), continuando solo con datos en vivo.
    Es idempotente: re-ejecutarlo no duplica muestras (dedup por position_id).
    """
    connector       = service.connector
    market_analyzer = service.market_analyzer
    if connector is None or market_analyzer is None or not connector.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no está conectado")

    try:
        from core.signal_filter import signal_filter

        date_from = datetime.now() - timedelta(days=days)
        trades = connector.get_closed_trades(from_date=date_from)
        if not trades:
            return {"message": "No se encontraron trades cerrados en el rango", "imported": 0}

        # win_rate progresivo por bot (replica self._stats: wins/trades, umbral 5)
        wr_state = {}   # strategy_id -> [wins, total]
        samples = []
        skipped_foreign = 0

        for t in trades:
            base = (t['magic'] // 10000) * 10000
            strategy_type = _MAGIC_BASE_TO_STRATEGY.get(base)
            if not strategy_type:
                skipped_foreign += 1
                continue  # trade manual u otro EA — no es de nuestros bots

            strategy_id = f"{strategy_type}_{t['symbol']}"
            state = wr_state.setdefault(strategy_id, [0, 0])
            win_rate = state[0] / state[1] if state[1] >= 5 else 0.5

            context = _reconstruct_context(connector, market_analyzer, t, win_rate)
            won = t['profit'] >= 0
            samples.append({
                **context,
                'strategy_id': strategy_id,
                'outcome':     1 if won else 0,
                'position_id': t['position_id'],
                'ts':          t['close_time'].isoformat(),
            })

            # actualizar estado de win_rate con el resultado de este trade
            state[1] += 1
            if won:
                state[0] += 1

        result = signal_filter.bulk_import_samples(samples)
        result['trades_evaluados']   = len(trades)
        result['descartados_ajenos'] = skipped_foreign
        result['rango_dias']         = days
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en backfill ML: {e}")


@router.post("/stats-backfill")
async def stats_backfill(days: int = 120, service: TradingService = Depends(get_trading_service)):
    """
    Reconstruye los archivos stats_{TIPO}_{SIMBOLO}.json desde el historial real
    de MT5, para que el filtro de rendimiento del RegimeDetector tenga win rates
    por bot DESDE YA (en vez de esperar 15 trades en vivo por cada uno).

    Agrupa los trades cerrados por bot (magic number → tipo + símbolo del deal),
    los procesa en orden cronológico y escribe wins/losses/trades_count + la
    ventana deslizante recent_results (últimos ROLLING_WINDOW_SIZE resultados).
    El P&L neto (comisiones + swap incluidos) determina win/loss (>= 0 = win),
    igual que update_stats() en vivo.

    Idempotente: sobrescribe con la verdad del historial MT5 (fuente autoritativa).
    Recomendado ejecutarlo una vez tras el despliegue.
    """
    import json
    from pathlib import Path
    from strategies.strategy_base import ROLLING_WINDOW_SIZE

    connector = service.connector
    if connector is None or not connector.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no está conectado")

    try:
        date_from = datetime.now() - timedelta(days=days)
        trades = connector.get_closed_trades(from_date=date_from)
        if not trades:
            return {"message": "No se encontraron trades cerrados en el rango", "archivos": 0}

        # strategy_id -> {'wins', 'losses', 'results': [1/0 cronológico]}
        by_bot = {}
        skipped_foreign = 0

        for t in trades:  # get_closed_trades ya viene ordenado por entry_time
            base = (t['magic'] // 10000) * 10000
            strategy_type = _MAGIC_BASE_TO_STRATEGY.get(base)
            if not strategy_type:
                skipped_foreign += 1
                continue

            strategy_id = f"{strategy_type}_{t['symbol']}"
            bucket = by_bot.setdefault(strategy_id, {'wins': 0, 'losses': 0, 'results': []})
            won = t['profit'] >= 0
            if won:
                bucket['wins'] += 1
            else:
                bucket['losses'] += 1
            bucket['results'].append(1 if won else 0)

        written = []
        for strategy_id, b in by_bot.items():
            total = b['wins'] + b['losses']
            payload = {
                "strategy_id":    strategy_id,
                "trades_count":   total,
                "wins":           b['wins'],
                "losses":         b['losses'],
                "avg_rr":         0.0,  # se afina con trades en vivo
                "recent_results": b['results'][-ROLLING_WINDOW_SIZE:],
                "last_updated":   datetime.now().isoformat(),
                "source":         "mt5_stats_backfill",
            }
            Path(f"stats_{strategy_id}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            wr = round(b['wins'] / total * 100, 1) if total else 0.0
            written.append({"bot": strategy_id, "trades": total,
                            "wins": b['wins'], "losses": b['losses'], "wr_pct": wr})

        # Orden: peores primero (los candidatos a que el filtro pause)
        written.sort(key=lambda x: (x['wr_pct'], -x['trades']))
        return {
            "archivos":          len(written),
            "trades_evaluados":  len(trades),
            "descartados_ajenos": skipped_foreign,
            "rango_dias":        days,
            "bots":              written,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en stats backfill: {e}")


def _default_comparison_combos() -> list:
    """
    Combinaciones estrategia×símbolo relevantes: las que bots_config.json
    tiene activas ahora mismo (lo que el RegimeDetector considera viable en
    la práctica), en vez del producto cruzado completo de todas las
    estrategias × todos los símbolos (72 combos, ~2h — no lo que se quiere
    para una comparación rápida y orientada a lo que el sistema realmente usa).
    """
    import json as _json
    from pathlib import Path as _Path
    cfg = _Path("bots_config.json")
    if not cfg.exists():
        return []
    try:
        data = _json.loads(cfg.read_text(encoding="utf-8"))
        seen = set()
        combos = []
        for entry in data:
            st, sym = entry.get("strategy_type"), entry.get("symbol")
            if st and sym and (st, sym) not in seen:
                seen.add((st, sym))
                combos.append((st, sym))
        return combos
    except Exception:
        return []


def _run_comparison_sync(service, combos: list, days: int, initial_balance: float, risk_pct: float) -> None:
    """
    Corre run_backtest() secuencialmente para cada combo. Se ejecuta en un
    hilo aparte (no bloquea el event loop) — cada backtest individual ya es
    una llamada sincrona pesada (~90s/combo medido para 1 año de H1).
    Actualiza _COMPARISON_STATE incrementalmente para que el GET pueda
    mostrar progreso mientras corre, y persiste el resultado final a disco.
    """
    import json as _json
    from pathlib import Path as _Path

    state = _COMPARISON_STATE
    for strategy_type, symbol in combos:
        try:
            result = service.run_backtest(
                symbol=symbol, strategy_type=strategy_type, days=days,
                initial_balance=initial_balance, risk_pct=risk_pct
            )
            if "error" in result:
                state["errors"].append({"strategy": strategy_type, "symbol": symbol, "error": result["error"]})
            else:
                result.pop("equity_curve", None)  # no hace falta en la comparación
                state["results"].append(result)
        except Exception as e:
            state["errors"].append({"strategy": strategy_type, "symbol": symbol, "error": str(e)})
        finally:
            state["completed"] += 1

    # Ranking: expectancia por trade (pnl promedio ponderado), luego profit factor.
    # Descarta combos con muy pocos trades (< 5) del ranking principal — no son
    # estadísticamente representativos, pero se conservan en la lista completa.
    for r in state["results"]:
        r["expectancy"] = round(r["total_pnl"] / r["trades"], 2) if r.get("trades") else 0.0
    state["results"].sort(
        key=lambda r: (r.get("trades", 0) >= 5, r.get("expectancy", -9999)),
        reverse=True
    )

    state["running"]     = False
    state["finished_at"] = datetime.now().isoformat()

    try:
        _Path(_COMPARISON_RESULTS_FILE).write_text(
            _json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


@router.post("/strategy-comparison")
async def start_strategy_comparison(
    days: int = 365,
    initial_balance: float = 1000.0,
    risk_pct: float = 0.02,
    service: TradingService = Depends(get_trading_service),
):
    """
    Lanza en background un backtest comparativo de las combinaciones
    estrategia×símbolo actualmente relevantes (las de bots_config.json).
    Devuelve de inmediato; consultar progreso/resultado con GET del mismo path.
    Con ~21 combos y 365 días, tarda del orden de ~30-40 minutos (medido:
    ~90s por combo para 1 año de H1).
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")
    if _COMPARISON_STATE["running"]:
        raise HTTPException(status_code=409, detail="Ya hay una comparación en curso")

    combos = _default_comparison_combos()
    if not combos:
        raise HTTPException(status_code=400, detail="No hay bots en bots_config.json para comparar")

    _COMPARISON_STATE.update({
        "running":      True,
        "started_at":   datetime.now().isoformat(),
        "finished_at":  None,
        "days":         days,
        "total_combos": len(combos),
        "completed":    0,
        "results":      [],
        "errors":       [],
    })

    import threading
    threading.Thread(
        target=_run_comparison_sync,
        args=(service, combos, days, initial_balance, risk_pct),
        daemon=True,
    ).start()

    return {
        "started":      True,
        "total_combos": len(combos),
        "combos":       [f"{st}_{sym}" for st, sym in combos],
        "days":         days,
        "eta_minutos":  round(len(combos) * 1.6, 1),
    }


@router.get("/strategy-comparison")
async def get_strategy_comparison():
    """
    Progreso/resultado del último barrido comparativo. Si no hay ninguno en
    memoria (ej. tras un reinicio), intenta cargar el último resultado
    guardado en disco.
    """
    if _COMPARISON_STATE["started_at"] is None:
        import json as _json
        from pathlib import Path as _Path
        f = _Path(_COMPARISON_RESULTS_FILE)
        if f.exists():
            try:
                return _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"running": False, "message": "Sin comparaciones ejecutadas todavía"}
    return _COMPARISON_STATE
