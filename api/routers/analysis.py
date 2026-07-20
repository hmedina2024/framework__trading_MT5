from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
import MetaTrader5 as mt5
from api.core.trading_service import TradingService

router = APIRouter()

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
