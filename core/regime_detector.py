"""
Clasificador de Régimen de Mercado — Fase 1A del Plan ML

Detecta el régimen actual de cada símbolo y decide qué estrategias
deben estar activas. Se ejecuta automáticamente cada día a las 07:00 UTC
(apertura de Londres) y cada 4 horas durante el día.

Regímenes:
  TRENDING_UP   — tendencia alcista fuerte
  TRENDING_DOWN — tendencia bajista fuerte
  RANGING       — mercado lateral
  VOLATILE      — alta volatilidad sin dirección

Estrategias por régimen:
  TRENDING_*  → EMA_CROSS, MACD, SUPERTREND, BREAKOUT
  RANGING     → BOLLINGER, WILLIAMS_R
  VOLATILE    → ninguna
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from utils.logger import get_logger

logger = get_logger(__name__)

ADX_TREND_THRESHOLD   = 22.0  # bajado de 25 — detecta tendencias antes
ADX_RANGE_THRESHOLD   = 18.0  # bajado de 20 — más selectivo para RANGING
ATR_VOLATILE_RATIO    = 2.0
EMA_SLOPE_PERIODS     = 10
REGIME_UPDATE_HOURS   = 4
REGIME_UPDATE_AT_OPEN = True

# Estrategias por régimen — máximo 2 por símbolo basado en datos reales.
# EMA_CROSS y MACD son las únicas consistentemente rentables en tendencia.
# Supertrend y Breakout generan 0 trades en H1 — reservados para H4/D1.
# En RANGING solo WILLIAMS_R porque Bollinger tiene WR < 25% en mercado actual.
STRATEGIES_BY_REGIME = {
    'TRENDING_UP':   ['EMA_CROSS', 'MACD'],
    'TRENDING_DOWN': ['EMA_CROSS', 'MACD'],
    'RANGING':       ['WILLIAMS_R'],   # Bollinger desactivado hasta WR > 40%
    'VOLATILE':      [],               # sin operaciones en volatilidad extrema
    'UNKNOWN':       ['EMA_CROSS'],    # solo la mejor estrategia como fallback
}

# Estrategias adicionales disponibles pero desactivadas por bajo rendimiento.
# Reactivar cuando los datos muestren mejora sostenida (mínimo 15 trades):
#   SUPERTREND  → 0 trades generados en H1, funciona mejor en H4
#   BREAKOUT    → 0 trades en H1, diseñado para H4 (ya configurado en SYMBOL_CONFIG)
#   BOLLINGER   → WR < 25% en entorno tendencial actual
STRATEGIES_INACTIVE = ['SUPERTREND', 'BREAKOUT', 'BOLLINGER']

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

# Estrategias preferidas por símbolo basadas en WR histórico real.
# Cuando el régimen detector inicia bots, prioriza estas estrategias.
# Si la estrategia preferida ya está activa, no inicia la segunda.
SYMBOL_PREFERRED_STRATEGY = {
    'EURUSD': 'EMA_CROSS',   # 60% WR histórico
    'GBPUSD': 'EMA_CROSS',   # 50% WR histórico
    'USDJPY': 'EMA_CROSS',   # rendimiento estable
    'XAUUSD': 'MACD',        # 62.5% WR histórico
    'AUDUSD': 'EMA_CROSS',   # 100% WR (1 trade — confirmar con más datos)
    'USDCAD': 'EMA_CROSS',   # 71.4% WR — mejor estrategia del sistema
    'US30':   'MACD',        # tendencia clara
    'BTCUSD': 'MACD',        # 75% WR histórico
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
        self._running = False
        self._thread: Optional[threading.Thread] = None
        logger.info("RegimeDetector inicializado")

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

            # Clasificación
            if atr_ratio >= ATR_VOLATILE_RATIO and adx < ADX_TREND_THRESHOLD:
                regime = 'VOLATILE'
            elif adx >= ADX_TREND_THRESHOLD:
                if price_above and ema200_slope > 0:
                    regime = 'TRENDING_UP'
                elif not price_above and ema200_slope < 0:
                    regime = 'TRENDING_DOWN'
                else:
                    regime = 'TRENDING_UP' if price_above else 'TRENDING_DOWN'
            elif adx < ADX_RANGE_THRESHOLD or bb_squeeze:
                regime = 'RANGING'
            else:
                if abs(ema200_slope) > 0.1:
                    regime = 'TRENDING_UP' if ema200_slope > 0 else 'TRENDING_DOWN'
                else:
                    regime = 'RANGING'

            result = {
                'regime':       regime,
                'adx':          round(adx, 1),
                'ema200_slope': round(ema200_slope, 3),
                'atr_ratio':    round(atr_ratio, 2),
                'bb_squeeze':   bb_squeeze,
                'price_vs_ema': 'above' if price_above else 'below',
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
        for symbol in SYMBOL_CONFIG:
            self.detect_regime(symbol)
        self._last_update = datetime.now(timezone.utc)
        return self._regimes

    def apply_regimes(self) -> Dict[str, List[str]]:
        """
        Inicia y detiene bots según el régimen actual de cada símbolo.
        Máximo 2 bots por símbolo. Prioriza las estrategias con mejor
        historial definidas en SYMBOL_PREFERRED_STRATEGY.
        Detiene automáticamente estrategias inactivas (SUPERTREND, BREAKOUT)
        que no generan trades en H1.
        """
        changes = {'started': [], 'stopped': []}
        MAX_BOTS_PER_SYMBOL = 2

        for symbol, regime_data in self._regimes.items():
            regime           = regime_data.get('regime', 'UNKNOWN')
            ideal_strategies = set(STRATEGIES_BY_REGIME.get(regime, []))
            active_map       = self._get_active_by_symbol(symbol)
            active_types     = set(active_map.values())

            # Detener estrategias inactivas (Supertrend, Breakout, Bollinger)
            # que no generan trades en el entorno actual
            for s_type in list(active_types):
                if s_type in STRATEGIES_INACTIVE:
                    s_id = f"{s_type}_{symbol}"
                    if self.trading_service.stop_strategy(s_id):
                        changes['stopped'].append(s_id)
                        active_types.discard(s_type)
                        logger.info(
                            f"Régimen: detenido {s_id} "
                            f"(estrategia inactiva — 0 trades en H1)"
                        )

            # Detener estrategias no aptas para el régimen actual
            to_stop = active_types - ideal_strategies
            for s_type in to_stop:
                s_id = f"{s_type}_{symbol}"
                if self.trading_service.stop_strategy(s_id):
                    changes['stopped'].append(s_id)
                    active_types.discard(s_type)
                    logger.info(
                        f"Régimen {regime}: detenido {s_id} "
                        f"(no apto para régimen actual)"
                    )

            # Iniciar estrategias solo si había bots activos y hay espacio
            if active_types or len(active_map) > 0:
                # Ordenar por preferencia del símbolo
                preferred = SYMBOL_PREFERRED_STRATEGY.get(symbol)
                ordered_strategies = []
                if preferred and preferred in ideal_strategies:
                    ordered_strategies.append(preferred)
                for s in ideal_strategies:
                    if s not in ordered_strategies:
                        ordered_strategies.append(s)

                # Solo iniciar hasta llegar al máximo de bots por símbolo
                current_active = set(self._get_active_by_symbol(symbol).values())
                for s_type in ordered_strategies:
                    if len(current_active) >= MAX_BOTS_PER_SYMBOL:
                        break
                    if s_type not in current_active:
                        if self.trading_service.start_strategy(symbol, s_type):
                            changes['started'].append(f"{s_type}_{symbol}")
                            current_active.add(s_type)
                            logger.info(
                                f"Régimen {regime}: iniciado {s_type} en {symbol} "
                                f"({len(current_active)}/{MAX_BOTS_PER_SYMBOL})"
                            )

        total = len(changes['started']) + len(changes['stopped'])
        if total > 0:
            logger.info(
                f"Cambios de régimen: {len(changes['started'])} iniciados, "
                f"{len(changes['stopped'])} detenidos — "
                f"máx {MAX_BOTS_PER_SYMBOL} bots/símbolo"
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

                # Apertura de Londres 07:00 UTC
                if REGIME_UPDATE_AT_OPEN and now_utc.hour == 7 and now_utc.minute < 5:
                    should_update = True

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
                'TRENDING_UP':   '🟢 TENDENCIA ↑',
                'TRENDING_DOWN': '🔴 TENDENCIA ↓',
                'RANGING':       '🟡 LATERAL',
                'VOLATILE':      '⚠️ VOLÁTIL',
                'UNKNOWN':       '❓ DESCONOCIDO',
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

    def _unknown(self, symbol: str) -> Dict:
        result = {
            'regime':     'UNKNOWN',
            'adx':        0,
            'strategies': STRATEGIES_BY_REGIME['UNKNOWN'],
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._regimes[symbol] = result
        return result