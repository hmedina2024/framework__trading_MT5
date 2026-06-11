"""
Clasificador de Régimen de Mercado — Fase 1A del Plan ML

5 niveles de régimen basados en la fuerza del ADX:

  RANGING_PURE     ADX < 18        → Bollinger + Williams %R + RSI
  RANGING_MILD     ADX 18-22       → Williams %R + MA_CROSS + RSI
  TRENDING_MILD    ADX 22-30       → EMA_CROSS + MACD
  TRENDING_STRONG  ADX 30-45       → EMA_CROSS + MACD + Supertrend
  TRENDING_EXTREME ADX > 45        → Breakout + Supertrend
  VOLATILE         ATR > 2x avg    → ninguna

Cada estrategia se activa solo en el contexto donde estadísticamente funciona.
Se ejecuta cada 4h y a las 07:00 UTC (apertura de Londres).
"""
import json
import MetaTrader5 as mt5
import pandas as pd
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Umbrales de clasificación ADX
# ---------------------------------------------------------------------------
ADX_RANGING_PURE     = 15.0   # ADX < 15  → lateral puro (Bollinger+Williams+RSI)
ADX_RANGING_MILD     = 20.0   # ADX 15-20 → lateral moderado (Williams+MA_CROSS+RSI)
ADX_TRENDING_MILD    = 25.0   # ADX 20-25 → tendencia moderada (EMA_CROSS+MACD)
ADX_TRENDING_STRONG  = 40.0   # ADX 25-40 → tendencia fuerte (EMA+MACD+SUPERTREND)
# ADX > 40 → tendencia extrema (BREAKOUT+SUPERTREND)
# ADX > 45            → tendencia extrema (Breakout + Supertrend)

ATR_VOLATILE_RATIO    = 2.0    # ATR actual > 2x promedio = volátil
EMA_SLOPE_PERIODS     = 10     # velas para calcular pendiente EMA200
REGIME_UPDATE_HOURS   = 4      # re-evaluar cada 4 horas
REGIME_UPDATE_AT_OPEN = True   # evaluar siempre a las 07:00 UTC

# Histéresis de régimen: el ADX debe superar el umbral de transición en al menos
# REGIME_HYSTERESIS puntos para que el régimen cambie. Previene que oscilaciones
# menores alrededor de un umbral (ej: ADX 21-23 cerca del límite 22) causen
# arranques y paradas continuas de bots cada 4 horas.
REGIME_HYSTERESIS = 2.0

# ---------------------------------------------------------------------------
# Estrategias por nivel de régimen
# Cada estrategia se activa SOLO en el contexto donde funciona bien.
# ---------------------------------------------------------------------------
STRATEGIES_BY_REGIME = {
    # ADX < 18 — mercado lateral puro, precio oscila en rango estrecho
    # Bollinger y Williams detectan reversiones, RSI confirma extremos
    'RANGING_PURE':     ['BOLLINGER', 'WILLIAMS_R', 'RSI'],

    # ADX 18-22 — lateral con ligera direccionalidad
    # Williams %R y RSI detectan reversiones; MA Cross se excluye porque genera
    # whipsaws en mercados sin tendencia (confirmado: 0% WR en datos reales)
    'RANGING_MILD':     ['WILLIAMS_R', 'RSI'],

    # ADX 22-30 — tendencia moderada, la más común en Forex
    # EMA Cross y MACD son los más rentables en este rango (WR 60-75%)
    # London ORB y NY ORB se activan aquí: impulso del open con tendencia moderada
    'TRENDING_MILD':    ['EMA_CROSS', 'MACD', 'LONDON_ORB', 'NY_ORB'],

    # ADX 30-45 — tendencia fuerte y sostenida
    # Supertrend + FVG: tendencia clara genera FVGs limpios y de alta continuación
    # NY ORB también funciona bien — el overlap Londres+NY tiene máximo volumen
    'TRENDING_STRONG':  ['EMA_CROSS', 'MACD', 'SUPERTREND', 'FVG', 'NY_ORB'],

    # ADX > 45 — tendencia extrema (eventos macro, noticias de alto impacto)
    # Breakout captura rupturas de rango, FVG captura retornos al desequilibrio
    'TRENDING_EXTREME': ['BREAKOUT', 'FVG'],

    # Alta volatilidad sin dirección — spread alto, riesgo extremo
    'VOLATILE':         [],

    # Fallback si no hay datos suficientes
    'UNKNOWN':          ['EMA_CROSS'],
}

# Máximo de bots activos simultáneos por símbolo
MAX_BOTS_PER_SYMBOL = 3

# ---------------------------------------------------------------------------
# Filtro de rendimiento por bot
# Un bot con historial suficiente y WR bajo no se inicia aunque el régimen sea
# correcto. Se re-evalúa en cada ciclo (cada 4h), por lo que un bot bloqueado
# puede volver a activarse si su WR mejora con el tiempo.
# ---------------------------------------------------------------------------
PERFORMANCE_MIN_TRADES   = 15    # trades mínimos para aplicar el filtro
PERFORMANCE_MIN_WR       = 0.38  # WR mínimo para INICIAR un bot
PERFORMANCE_STOP_WR      = 0.30  # WR mínimo para MANTENER un bot corriendo

# Ventana deslizante: cuántos trades recientes usar para evaluar WR.
# Debe coincidir con ROLLING_WINDOW_SIZE de strategy_base.py.
ROLLING_WINDOW_SIZE      = 20

# Horas de pausa obligatoria antes de dar al bot una segunda oportunidad.
# Tras este período, el bot puede arrancar de nuevo si el régimen lo requiere.
# Si en la siguiente evaluación su WR reciente sigue bajo, se pausa otra vez.
PERFORMANCE_RETRY_HOURS  = 48

BLOCKED_STATE_FILE = Path("performance_blocked_state.json")

# ---------------------------------------------------------------------------
# Configuración de timeframe por símbolo
# ---------------------------------------------------------------------------
SYMBOL_CONFIG = {
    'EURUSD': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'GBPUSD': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'USDJPY': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'XAUUSD': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'AUDUSD': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'USDCAD': {'timeframe': mt5.TIMEFRAME_H1,  'atr_period': 14},
    'US30':   {'timeframe': mt5.TIMEFRAME_H4,  'atr_period': 14},
    'BTCUSD': {'timeframe': mt5.TIMEFRAME_H4,  'atr_period': 14},
}

# ---------------------------------------------------------------------------
# Estrategia preferida por símbolo (basada en WR histórico real)
# Se prioriza al iniciar bots — ocupa el primer slot disponible
# ---------------------------------------------------------------------------
SYMBOL_PREFERRED_STRATEGY = {
    'EURUSD': 'EMA_CROSS',    # 60% WR histórico
    'GBPUSD': 'LONDON_ORB',   # ORB es ideal para GBP en London Open
    'USDJPY': 'EMA_CROSS',    # rendimiento estable
    'XAUUSD': 'MACD',         # 83% WR histórico — mantener el mejor bot activo
    'AUDUSD': 'EMA_CROSS',    # 100% WR (confirmar con más trades)
    'USDCAD': 'EMA_CROSS',    # 71.4% WR — mejor del sistema
    'US30':   'BREAKOUT',     # H4 — Breakout en tendencias extremas
    'BTCUSD': 'MACD',         # 75% WR histórico
}


class RegimeDetector:
    """
    Detecta régimen de mercado y gestiona qué bots deben estar activos.
    """

    def __init__(self, market_analyzer, trading_service):
        self.market_analyzer = market_analyzer
        self.trading_service = trading_service
        self._regimes: Dict[str, Dict] = {}
        self._last_update: Optional[datetime] = None
        self._last_london_open_date: Optional[object] = None  # evita disparar 5x en 07:00-07:04
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # {strategy_id: blocked_until_datetime} — bots pausados por bajo rendimiento
        self._performance_blocked: Dict[str, datetime] = {}
        self._load_blocked_state()
        logger.info("RegimeDetector inicializado")

    # -----------------------------------------------------------------------
    # Clasificación ADX con histéresis
    # -----------------------------------------------------------------------

    def _classify_adx_plain(self, adx: float) -> str:
        """Clasificación directa por umbrales sin histéresis."""
        if adx > ADX_TRENDING_STRONG:  return 'TRENDING_EXTREME'
        if adx > ADX_TRENDING_MILD:    return 'TRENDING_STRONG'
        if adx > ADX_RANGING_MILD:     return 'TRENDING_MILD'
        if adx > ADX_RANGING_PURE:     return 'RANGING_MILD'
        return 'RANGING_PURE'

    def _classify_adx_with_hysteresis(
        self, adx: float, atr_ratio: float, previous_regime: str
    ) -> str:
        """
        Clasifica el régimen aplicando histéresis respecto al régimen anterior.
        Para moverse a un régimen superior, el ADX debe superar el umbral + H.
        Para moverse a un régimen inferior, el ADX debe caer bajo el umbral - H.
        Esto previene oscilaciones cuando el ADX ronda un umbral de transición.
        El régimen VOLATILE y UNKNOWN no aplican histéresis (seguridad prioritaria).
        """
        h = REGIME_HYSTERESIS

        # Volátil: siempre tiene prioridad — no aplica histéresis
        if atr_ratio >= ATR_VOLATILE_RATIO and adx < ADX_TRENDING_MILD:
            return 'VOLATILE'

        # Sin régimen previo: clasificar sin histéresis
        if previous_regime in ('UNKNOWN', 'VOLATILE'):
            return self._classify_adx_plain(adx)

        if previous_regime == 'RANGING_PURE':
            # Para salir hacia arriba: necesita ADX > umbral + H
            if adx > ADX_RANGING_PURE + h:
                return self._classify_adx_plain(adx)
            return 'RANGING_PURE'

        if previous_regime == 'RANGING_MILD':
            if adx > ADX_RANGING_MILD + h:
                return self._classify_adx_plain(adx)
            if adx < ADX_RANGING_PURE - h:
                return 'RANGING_PURE'
            return 'RANGING_MILD'

        if previous_regime == 'TRENDING_MILD':
            if adx > ADX_TRENDING_MILD + h:
                return self._classify_adx_plain(adx)
            if adx < ADX_RANGING_MILD - h:
                return self._classify_adx_plain(adx)
            return 'TRENDING_MILD'

        if previous_regime == 'TRENDING_STRONG':
            if adx > ADX_TRENDING_STRONG + h:
                return 'TRENDING_EXTREME'
            if adx < ADX_TRENDING_MILD - h:
                return self._classify_adx_plain(adx)
            return 'TRENDING_STRONG'

        if previous_regime == 'TRENDING_EXTREME':
            if adx < ADX_TRENDING_STRONG - h:
                return self._classify_adx_plain(adx)
            return 'TRENDING_EXTREME'

        return self._classify_adx_plain(adx)

    # -----------------------------------------------------------------------

    def get_regime(self, symbol: str) -> str:
        return self._regimes.get(symbol, {}).get('regime', 'UNKNOWN')

    def get_all_regimes(self) -> Dict[str, Dict]:
        return dict(self._regimes)

    def detect_regime(self, symbol: str) -> Dict:
        try:
            cfg       = SYMBOL_CONFIG.get(symbol, {'timeframe': mt5.TIMEFRAME_H1, 'atr_period': 14})
            timeframe = cfg['timeframe']

            df = self.market_analyzer.get_candles(symbol, timeframe, count=230)
            if df is None or len(df) < 50:
                return self._unknown(symbol)

            # ADX
            adx = self.market_analyzer.calculate_adx(df, period=14)
            if adx is None:
                return self._unknown(symbol)

            # EMA200 pendiente y posición del precio
            ema200         = self.market_analyzer.calculate_ema(df, 200)
            current_price  = df['close'].iloc[-1]
            ema200_current = ema200.iloc[-1]
            ema200_prev    = ema200.iloc[-EMA_SLOPE_PERIODS]

            if pd.isna(ema200_current) or pd.isna(ema200_prev) or ema200_prev == 0:
                return self._unknown(symbol)

            ema200_slope   = (ema200_current - ema200_prev) / ema200_prev * 100
            price_above    = current_price > ema200_current

            # ATR ratio (volatilidad actual vs promedio 20 períodos)
            atr_series  = self.market_analyzer.calculate_atr(df, period=14)
            atr_current = atr_series.iloc[-1]
            atr_avg     = atr_series.iloc[-20:].mean()
            atr_ratio   = atr_current / atr_avg if atr_avg > 0 else 1.0

            # Bollinger squeeze (bandas estrechas = lateral)
            bb_upper, bb_middle, bb_lower = self.market_analyzer.calculate_bollinger_bands(df, 20, 2.0)
            bb_width     = (bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_middle.iloc[-1] * 100
            bb_width_avg = ((bb_upper - bb_lower) / bb_middle * 100).iloc[-20:].mean()
            bb_squeeze   = bb_width < (bb_width_avg * 0.8)

            # ----------------------------------------------------------------
            # Clasificación en 5 niveles con histéresis respecto al régimen anterior
            # ----------------------------------------------------------------
            previous_regime = self._regimes.get(symbol, {}).get('regime', 'UNKNOWN')
            regime = self._classify_adx_with_hysteresis(adx, atr_ratio, previous_regime)

            if regime != previous_regime and previous_regime not in ('UNKNOWN', 'VOLATILE'):
                logger.info(
                    f"Régimen {symbol}: {previous_regime} → {regime} "
                    f"(ADX={adx:.1f}, H={REGIME_HYSTERESIS})"
                )

            result = {
                'regime':       regime,
                'adx':          round(adx, 1),
                'adx_level':    (
                    'EXTREME'      if adx > ADX_TRENDING_STRONG else
                    'STRONG'       if adx > ADX_TRENDING_MILD   else
                    'MILD'         if adx > ADX_RANGING_MILD    else
                    'RANGING_MILD' if adx > ADX_RANGING_PURE    else
                    'RANGING_PURE'
                ),
                'ema200_slope': round(ema200_slope, 3),
                'atr_ratio':    round(atr_ratio, 2),
                'bb_squeeze':   bb_squeeze,
                'price_vs_ema': 'above' if price_above else 'below',
                'direction':    'UP' if price_above else 'DOWN',
                'strategies':   STRATEGIES_BY_REGIME.get(regime, []),
                'updated_at':   datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                f"Régimen {symbol}: {regime} | ADX={adx:.1f} | "
                f"slope={ema200_slope:.3f}% | ATR={atr_ratio:.2f}x | "
                f"→ {result['strategies']}"
            )
            self._regimes[symbol] = result
            return result

        except Exception as e:
            logger.error(f"Error detectando régimen {symbol}: {e}", exc_info=True)
            return self._unknown(symbol)

    def detect_all_regimes(self) -> Dict[str, Dict]:
        logger.info("Detectando regímenes para todos los símbolos...")
        # Actualizar estrategia preferida por símbolo con datos reales antes de detectar
        try:
            self._update_preferred_strategies()
        except Exception as e:
            logger.warning(f"_update_preferred_strategies error: {e}")
        for symbol in SYMBOL_CONFIG:
            self.detect_regime(symbol)
        self._last_update = datetime.now(timezone.utc)
        return self._regimes

    def apply_regimes(self) -> Dict[str, List[str]]:
        """
        Inicia y detiene bots según el régimen actual y el rendimiento real de cada bot.

        Lógica:
          1. Detiene bots cuyo tipo no aplica al régimen actual.
          2. Detiene bots activos con WR crónicamente bajo (< PERFORMANCE_STOP_WR).
          3. Inicia bots aptos para el régimen, priorizando los de mejor WR real.
             Bots con WR < PERFORMANCE_MIN_WR (y suficientes trades) no se inician.
          4. Actualiza SYMBOL_PREFERRED_STRATEGY con el mejor performer real.
        """
        changes = {'started': [], 'stopped': []}

        for symbol, regime_data in self._regimes.items():
            regime           = regime_data.get('regime', 'UNKNOWN')
            ideal_strategies = set(STRATEGIES_BY_REGIME.get(regime, []))
            active_map       = self._get_active_by_symbol(symbol)
            active_types     = set(active_map.values())

            # 1. Detener bots no aptos para el régimen actual
            to_stop_regime = active_types - ideal_strategies
            for s_type in to_stop_regime:
                s_id = f"{s_type}_{symbol}"
                if self.trading_service.stop_strategy(s_id):
                    changes['stopped'].append(s_id)
                    active_types.discard(s_type)
                    logger.info(f"Régimen {regime}: detenido {s_id} (fuera de régimen)")

            # 2. Detener bots activos con rendimiento crónicamente bajo
            for s_type in list(active_types):
                stop, stop_reason = self._should_stop_bot(s_type, symbol)
                if stop:
                    s_id = f"{s_type}_{symbol}"
                    if self.trading_service.stop_strategy(s_id):
                        changes['stopped'].append(s_id)
                        active_types.discard(s_type)
                        # Registrar pausa con temporizador de reintentos
                        retry_at = datetime.now() + timedelta(hours=PERFORMANCE_RETRY_HOURS)
                        self._performance_blocked[s_id] = retry_at
                        self._save_blocked_state()
                        logger.warning(
                            f"Bot detenido por bajo rendimiento: {s_id} — {stop_reason} | "
                            f"segunda oportunidad a las {retry_at.strftime('%d/%m %H:%M')}"
                        )
                        try:
                            from utils.telegram_notifier import send_message
                            send_message(
                                f"📉 <b>Bot pausado por rendimiento</b>\n"
                                f"Bot: <b>{s_id}</b>\n"
                                f"Motivo: {stop_reason}\n"
                                f"Segunda oportunidad: <b>{retry_at.strftime('%d/%m/%Y %H:%M')}</b>",
                                silent=True
                            )
                        except Exception:
                            pass

            # 3. Ordenar candidatos por WR real (mejor primero), con preferido como desempate
            preferred = SYMBOL_PREFERRED_STRATEGY.get(symbol)
            scored: List[tuple] = []
            for s_type in ideal_strategies:
                stats = self._load_bot_stats(s_type, symbol)
                wr    = stats['win_rate'] if stats['win_rate'] is not None else 0.5
                bonus = 0.02 if s_type == preferred else 0.0
                scored.append((s_type, wr + bonus, stats['trades_count']))
            # Primero los de mayor WR; si sin datos (< min_trades), al final
            scored.sort(key=lambda x: (-x[1], x[2] < PERFORMANCE_MIN_TRADES))

            # 4. Iniciar bots hasta el límite, respetando filtro de rendimiento
            current_active = set(self._get_active_by_symbol(symbol).values())
            for s_type, _score, _trades in scored:
                if len(current_active) >= MAX_BOTS_PER_SYMBOL:
                    break
                if s_type in current_active:
                    continue
                can_start, perf_reason = self._can_start_bot(s_type, symbol)
                if not can_start:
                    logger.info(
                        f"Régimen {regime}: omitiendo {s_type}_{symbol} — {perf_reason}"
                    )
                    continue
                if self.trading_service.start_strategy(symbol, s_type):
                    changes['started'].append(f"{s_type}_{symbol}")
                    current_active.add(s_type)
                    logger.info(
                        f"Régimen {regime}: iniciado {s_type} en {symbol} "
                        f"({len(current_active)}/{MAX_BOTS_PER_SYMBOL}) — {perf_reason}"
                    )

        total = len(changes['started']) + len(changes['stopped'])
        if total > 0:
            logger.info(
                f"Cambios de régimen: {len(changes['started'])} iniciados, "
                f"{len(changes['stopped'])} detenidos"
            )
        return changes

    def start_scheduler(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="RegimeScheduler"
        )
        self._thread.start()
        logger.info(
            f"RegimeDetector scheduler iniciado "
            f"(cada {REGIME_UPDATE_HOURS}h + 07:00 UTC)"
        )

    def stop_scheduler(self):
        self._running = False

    def _scheduler_loop(self):
        # Primera evaluación inmediata al arrancar
        try:
            self.detect_all_regimes()
            self.apply_regimes()
            self._notify_regime_update()
        except Exception as e:
            logger.error(f"RegimeDetector arranque error: {e}")

        while self._running:
            try:
                now_utc = datetime.now(timezone.utc)
                should_update = False

                # Apertura de Londres 07:00 UTC — solo una vez por día
                if REGIME_UPDATE_AT_OPEN and now_utc.hour == 7 and now_utc.minute < 5:
                    if self._last_london_open_date != now_utc.date():
                        should_update = True
                        self._last_london_open_date = now_utc.date()

                # Cada N horas
                if self._last_update:
                    hours_since = (now_utc - self._last_update).total_seconds() / 3600
                    if hours_since >= REGIME_UPDATE_HOURS:
                        should_update = True

                if should_update:
                    logger.info("RegimeDetector: actualizando regímenes...")
                    self.detect_all_regimes()
                    self.apply_regimes()
                    self._notify_regime_update()

                time.sleep(60)

            except Exception as e:
                logger.error(f"RegimeDetector scheduler error: {e}")
                time.sleep(60)

    def _notify_regime_update(self):
        try:
            from utils.telegram_notifier import send_message
            emoji_map = {
                'TRENDING_EXTREME': '🚀 TENDENCIA EXTREMA',
                'TRENDING_STRONG':  '🟢 TENDENCIA FUERTE',
                'TRENDING_MILD':    '🔵 TENDENCIA MODERADA',
                'RANGING_MILD':     '🟡 LATERAL MODERADO',
                'RANGING_PURE':     '⚪ LATERAL PURO',
                'VOLATILE':         '⚠️ VOLÁTIL',
                'UNKNOWN':          '❓ DESCONOCIDO',
            }
            lines = ["📊 <b>Régimen de mercado actualizado</b>\n"]
            for symbol, data in self._regimes.items():
                regime    = data.get('regime', 'UNKNOWN')
                adx       = data.get('adx', 0)
                strategies = data.get('strategies', [])
                label     = emoji_map.get(regime, regime)
                strats    = ', '.join(strategies) if strategies else 'ninguna — pausado'
                lines.append(f"<b>{symbol}</b> {label} (ADX {adx})\n  → {strats}")
            send_message('\n'.join(lines), silent=True)
        except Exception:
            pass

    def _get_active_by_symbol(self, symbol: str) -> Dict[str, str]:
        """Retorna {strategy_id: strategy_type} para el símbolo."""
        result = {}
        for s_id in self.trading_service.active_strategies:
            for catalog_type in self.trading_service.STRATEGY_CATALOG:
                if s_id.startswith(f"{catalog_type}_"):
                    s_symbol = s_id[len(f"{catalog_type}_"):]
                    if s_symbol == symbol:
                        result[s_id] = catalog_type
        return result

    # -----------------------------------------------------------------------
    # Gestión adaptativa por rendimiento
    # -----------------------------------------------------------------------

    def _load_blocked_state(self) -> None:
        """Carga el estado de bloqueo de bots desde disco al arrancar."""
        if not BLOCKED_STATE_FILE.exists():
            return
        try:
            data = json.loads(BLOCKED_STATE_FILE.read_text(encoding='utf-8'))
            now  = datetime.now()
            for sid, dt_str in data.items():
                blocked_until = datetime.fromisoformat(dt_str)
                if blocked_until > now:
                    self._performance_blocked[sid] = blocked_until
            expired = len(data) - len(self._performance_blocked)
            logger.info(
                f"RegimeDetector: {len(self._performance_blocked)} bots en pausa "
                f"cargados desde disco ({expired} expirados ignorados)"
            )
        except Exception as e:
            logger.warning(f"No se pudo cargar estado de bloqueo: {e}")

    def _save_blocked_state(self) -> None:
        """Persiste el estado de bloqueo para que sobreviva reinicios del servidor."""
        try:
            data = {sid: dt.isoformat() for sid, dt in self._performance_blocked.items()}
            BLOCKED_STATE_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error(f"Error guardando estado de bloqueo: {e}")

    def _load_bot_stats(self, strategy_type: str, symbol: str) -> Dict:
        """
        Lee stats_{TIPO_SIMBOLO}.json y retorna métricas de rendimiento.
        Devuelve tanto el WR total (historial completo) como el WR de la
        ventana deslizante (últimos ROLLING_WINDOW_SIZE trades).
        """
        stats_file = Path(f"stats_{strategy_type}_{symbol}.json")
        default = {
            'trades_count': 0, 'wins': 0, 'losses': 0,
            'win_rate': None, 'recent_win_rate': None, 'recent_count': 0,
        }
        if not stats_file.exists():
            return default
        try:
            data   = json.loads(stats_file.read_text(encoding='utf-8'))
            trades = int(data.get('trades_count', 0))
            wins   = int(data.get('wins', 0))

            # Ventana deslizante
            recent        = data.get('recent_results', [])
            recent_count  = len(recent)
            recent_wr     = sum(recent) / recent_count if recent_count >= 5 else None

            return {
                'trades_count':   trades,
                'wins':           wins,
                'losses':         int(data.get('losses', 0)),
                'win_rate':       wins / trades if trades > 0 else None,
                'recent_win_rate': recent_wr,
                'recent_count':   recent_count,
            }
        except Exception:
            return default

    def _can_start_bot(self, strategy_type: str, symbol: str) -> tuple:
        """
        (can_start: bool, reason: str)

        Orden de evaluación:
          1. Pausa por rendimiento activa → bloquear hasta que expire.
          2. Pausa expirada → segunda oportunidad: limpiar bloqueo y permitir.
          3. WR de ventana deslizante < PERFORMANCE_MIN_WR → no iniciar.
          4. Pocos datos (< PERFORMANCE_MIN_TRADES) → permitir (bot nuevo).
          5. WR aceptable → permitir.
        """
        strategy_id   = f"{strategy_type}_{symbol}"
        blocked_until = self._performance_blocked.get(strategy_id)

        if blocked_until:
            now = datetime.now()
            if now < blocked_until:
                remaining_h = (blocked_until - now).total_seconds() / 3600
                return False, (
                    f"en pausa por rendimiento — "
                    f"segunda oportunidad en {remaining_h:.0f}h "
                    f"(a las {blocked_until.strftime('%d/%m %H:%M')})"
                )
            # Pausa expirada → limpiar y dar segunda oportunidad
            del self._performance_blocked[strategy_id]
            self._save_blocked_state()
            logger.info(
                f"Bot {strategy_id}: pausa de rendimiento expirada — "
                f"segunda oportunidad activa"
            )

        stats        = self._load_bot_stats(strategy_type, symbol)
        recent_wr    = stats['recent_win_rate']
        recent_count = stats['recent_count']
        total_trades = stats['trades_count']

        # Usar WR de ventana deslizante si hay suficientes datos recientes
        if recent_wr is not None and recent_count >= PERFORMANCE_MIN_TRADES:
            if recent_wr < PERFORMANCE_MIN_WR:
                return False, (
                    f"WR reciente {recent_wr*100:.0f}% "
                    f"(últimos {recent_count} trades, mínimo {PERFORMANCE_MIN_WR*100:.0f}%)"
                )
            return True, f"WR reciente {recent_wr*100:.0f}% ({recent_count} trades)"

        # Fallback a WR total si ventana insuficiente
        if total_trades < PERFORMANCE_MIN_TRADES:
            return True, f"bot nuevo ({total_trades} trades — sin filtro de WR)"

        wr = stats['win_rate']
        if wr is not None and wr < PERFORMANCE_MIN_WR:
            return False, (
                f"WR total {wr*100:.0f}% en {total_trades} trades "
                f"(mínimo {PERFORMANCE_MIN_WR*100:.0f}%)"
            )
        return True, f"WR total {wr*100:.0f}% ({total_trades} trades)"

    def _should_stop_bot(self, strategy_type: str, symbol: str) -> tuple:
        """
        (should_stop: bool, reason: str)
        Prioriza el WR de la ventana deslizante (más relevante) sobre el WR total.
        Umbral de parada es más permisivo que el de inicio para evitar churning.
        """
        stats        = self._load_bot_stats(strategy_type, symbol)
        recent_wr    = stats['recent_win_rate']
        recent_count = stats['recent_count']
        total_trades = stats['trades_count']

        # Evaluar con ventana deslizante si hay suficientes datos
        if recent_wr is not None and recent_count >= PERFORMANCE_MIN_TRADES:
            if recent_wr < PERFORMANCE_STOP_WR:
                return True, (
                    f"WR reciente {recent_wr*100:.0f}% "
                    f"(últimos {recent_count} trades, umbral {PERFORMANCE_STOP_WR*100:.0f}%)"
                )
            return False, ""

        # Fallback a WR total
        if total_trades < PERFORMANCE_MIN_TRADES:
            return False, ""

        wr = stats['win_rate']
        if wr is not None and wr < PERFORMANCE_STOP_WR:
            return True, (
                f"WR total {wr*100:.0f}% en {total_trades} trades "
                f"(umbral {PERFORMANCE_STOP_WR*100:.0f}%)"
            )
        return False, ""

    def _update_preferred_strategies(self) -> None:
        """
        Actualiza SYMBOL_PREFERRED_STRATEGY dinámicamente según el WR real
        de cada combinación estrategia×símbolo. Si una estrategia supera al
        preferido actual con suficientes trades, lo reemplaza.
        Garantiza que el primer slot de cada símbolo esté ocupado por el
        mejor bot disponible según datos reales.
        """
        all_types = set(s for strategies in STRATEGIES_BY_REGIME.values() for s in strategies)

        for symbol in SYMBOL_CONFIG:
            best_type  = None
            best_wr    = -1.0

            for s_type in all_types:
                stats = self._load_bot_stats(s_type, symbol)
                if stats['trades_count'] < PERFORMANCE_MIN_TRADES:
                    continue
                wr = stats['win_rate']
                if wr is not None and wr > best_wr:
                    best_wr   = wr
                    best_type = s_type

            if best_type and best_wr >= PERFORMANCE_MIN_WR:
                current = SYMBOL_PREFERRED_STRATEGY.get(symbol)
                if current != best_type:
                    logger.info(
                        f"Estrategia preferida {symbol}: "
                        f"{current} → {best_type} (WR {best_wr*100:.0f}% real)"
                    )
                    SYMBOL_PREFERRED_STRATEGY[symbol] = best_type

    def _unknown(self, symbol: str) -> Dict:
        result = {
            'regime':     'UNKNOWN',
            'adx':        0,
            'adx_level':  'UNKNOWN',
            'direction':  'UNKNOWN',
            'strategies': STRATEGIES_BY_REGIME['UNKNOWN'],
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._regimes[symbol] = result
        return result