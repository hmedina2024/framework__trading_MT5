"""
Clase base para estrategias de trading

Mejoras aplicadas vs version anterior:
  1. Cooldown post-trade por simbolo (COOLDOWN_HOURS_AFTER_TRADE)
     - tras cerrar una posicion, bloquea nuevas entradas N horas en ese simbolo
  2. Limite de trades diarios por simbolo (MAX_DAILY_TRADES_PER_SYMBOL)
     - evita over-trading (ej: 10 trades en GBPUSD en 1 hora)
  3. Edad minima de posicion antes de evaluar salida (MIN_POSITION_AGE_SECONDS)
     - evita cerrar en la misma vela/iteracion que se abrio
  4. Registro de tiempo de apertura por ticket (_position_open_times)
  5. cooldowns_active visible en get_statistics() para monitoreo desde frontend
  6. Persistencia de stats en disco (stats_{strategy_id}.json)
     - trades_count, wins, losses sobreviven reinicios del servidor
     - el auto-escalado de riesgo mantiene su historial entre sesiones
     - carga automatica en start(), guardado automatico en update_stats()
"""
import threading
import time
import json
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from utils.logger import get_logger
from models.trade_models import TradeRequest, OrderType, Position
try:
    from utils.telegram_notifier import (
        alert_trade_opened, alert_trade_closed, alert_balance_drop
    )
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False

logger = get_logger(__name__)

# Circuit breaker — importación lazy para evitar circular imports en tests
try:
    from core.circuit_breaker import circuit_breaker as _circuit_breaker
    _CB_AVAILABLE = True
except ImportError:
    _circuit_breaker = None
    _CB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Parametros de proteccion — ajustar segun necesidades
# ---------------------------------------------------------------------------

# Horas sin operar en un simbolo tras cerrar una posicion
# DEMO: reducido de 2h a 0.5h (30 min)
COOLDOWN_HOURS_AFTER_TRADE = 0.5

# Maximo de trades por simbolo en el mismo dia calendario
# DEMO: aumentado de 3 a 10
MAX_DAILY_TRADES_PER_SYMBOL = 10

# Segundos minimos que debe vivir una posicion antes de evaluar el cierre
MIN_POSITION_AGE_SECONDS = 300

# Trailing Stop: multiplicador de ATR para mover el SL
# Cuando la posicion gana >= TRAILING_ACTIVATION_ATR * ATR, se activa el trailing
# El SL se mueve a precio_actual - TRAILING_STOP_ATR * ATR (para BUY)
# En False lo desactiva — cambiar a True para activar en todas las estrategias
TRAILING_STOP_ENABLED   = True
TRAILING_ACTIVATION_ATR = 1.0   # activar cuando ganancia >= 1x ATR
TRAILING_STOP_ATR       = 1.0   # SL se coloca a 1x ATR del precio actual

# ---------------------------------------------------------------------------
# Filtro de sesion de trading
# Solo opera durante las sesiones de mayor liquidez (hora del servidor UTC+2)
# Sesion Londres:    07:00 - 16:00 UTC  = 09:00 - 18:00 UTC+2
# Sesion NY:         13:00 - 20:00 UTC  = 15:00 - 22:00 UTC+2
# Overlap Londres+NY (mayor volumen): 13:00 - 16:00 UTC = 15:00 - 18:00 UTC+2
# Fuera de sesion: Asia (00:00-07:00 UTC) — alta tasa de señales falsas
# ---------------------------------------------------------------------------
TRADING_SESSION_FILTER  = True   # False = operar 24h (modo sin restriccion)
SESSION_START_UTC       = 7      # hora UTC de inicio (apertura Londres)
SESSION_END_UTC         = 20     # hora UTC de cierre (cierre NY)

# ---------------------------------------------------------------------------
# Filtro de correlación entre pares
# Pares con correlación histórica alta (>0.70) comparten el mismo movimiento
# del dólar. Si ya hay posición abierta en uno, no abrir en el correlacionado.
# Grupos: si hay BUY en EURUSD, bloquea BUY en AUDUSD/GBPUSD (misma dirección USD)
#         si hay SELL en EURUSD, bloquea SELL en AUDUSD/GBPUSD
# ---------------------------------------------------------------------------
CORRELATION_FILTER = True

# Grupos de pares correlacionados (todos se mueven similar vs el USD)
CORRELATED_GROUPS = [
    {'EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD'},   # largo USD inverso
    {'USDJPY', 'USDCAD', 'USDCHF'},              # largo USD directo
]

# ---------------------------------------------------------------------------
# Filtro de noticias de alto impacto
# Bloquea nuevas entradas N minutos antes y después de eventos macro clave.
# Fuente: ForexFactory calendar API (gratuita, sin autenticación).
# ---------------------------------------------------------------------------
NEWS_FILTER_ENABLED = True
NEWS_MINUTES_BEFORE = 30   # bloquear 30 min antes del evento
NEWS_MINUTES_AFTER  = 30   # bloquear 30 min después del evento
NEWS_CALENDAR_URL   = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_CACHE_MINUTES  = 60   # refrescar calendario cada hora

# ---------------------------------------------------------------------------
# Filtro de spread máximo por símbolo
# Bloquea entradas cuando el spread supera el umbral (durante noticias, apertura
# de mercados, fines de semana o periodos de baja liquidez).
# Valores en unidades de precio (no pips) para ser agnóstico al instrumento.
# ---------------------------------------------------------------------------
SPREAD_FILTER_ENABLED = True

MAX_SPREAD = {
    'EURUSD': 0.00030,   # 3 pips
    'GBPUSD': 0.00040,   # 4 pips
    'USDJPY': 0.030,     # 3 pips (JPY)
    'XAUUSD': 0.50,      # $0.50
    'AUDUSD': 0.00035,
    'USDCAD': 0.00035,
    'US30':   5.0,       # 5 puntos
    'BTCUSD': 30.0,      # $30
}
DEFAULT_MAX_SPREAD = 0.00050

# ---------------------------------------------------------------------------
# Filtro de alineación multi-timeframe (H4)
# Solo permite entradas cuya dirección es coherente con la tendencia H4.
# Usa la pendiente de EMA50 en H4: si el slope supera el umbral, la tendencia
# es clara y se bloquean operaciones en contra de ella.
# Esto reduce drasticamente las señales falsas en estrategias de tendencia.
# ---------------------------------------------------------------------------
MTF_FILTER_ENABLED  = True
MTF_SLOPE_THRESHOLD = 0.04   # % de pendiente EMA50-H4 necesario para considerar tendencia

# ---------------------------------------------------------------------------
# Ajuste dinámico de SL según volatilidad del día
# Si el ATR actual supera ATR_HIGH_VOL_THRESHOLD veces el ATR promedio de 20 días,
# se amplía el multiplicador de SL para evitar ser barrido por ruido.
# Ejemplo: día de NFP el ATR puede ser 2-3x el promedio — sin ajuste el SL
# se golpea en minutos aunque la dirección sea correcta.
# ---------------------------------------------------------------------------
ATR_VOLATILITY_ENABLED       = True
ATR_HIGH_VOL_THRESHOLD       = 1.5   # ATR actual > 1.5x promedio = alta volatilidad
ATR_HIGH_VOL_SL_MULTIPLIER   = 1.5   # ampliar SL x1.5 en días de alta volatilidad
ATR_LOW_VOL_THRESHOLD        = 0.7   # ATR actual < 0.7x promedio = baja volatilidad
ATR_LOW_VOL_SL_MULTIPLIER    = 0.85  # reducir SL x0.85 en días tranquilos

# ---------------------------------------------------------------------------
# Escalado automático de posición en estrategias ganadoras
# Si una estrategia supera WIN_RATE_THRESHOLD con al menos MIN_TRADES_TO_SCALE
# trades, el riesgo por trade sube gradualmente hasta MAX_RISK_SCALED.
# El escalado es conservador: sube de a 0.1% por nivel para no sobre-exponer.
# Criterios para escalar:
#   - Win rate >= WIN_RATE_THRESHOLD (default 65%)
#   - Al menos MIN_TRADES_TO_SCALE trades (default 10) — base estadística mínima
#   - Riesgo máximo escalado: MAX_RISK_SCALED (default 2.0%)
# Criterios para reducir:
#   - Win rate cae bajo WIN_RATE_REDUCE (default 45%) — reducir a riesgo base
# ---------------------------------------------------------------------------
AUTO_SCALE_ENABLED      = True
WIN_RATE_THRESHOLD      = 0.65   # 65% WR para empezar a escalar
WIN_RATE_REDUCE         = 0.45   # 45% WR para reducir al riesgo base
MIN_TRADES_TO_SCALE     = 10     # mínimo de trades para evaluar escalado
MAX_RISK_SCALED         = 0.02   # tope máximo: 2% aunque WR sea 100%
SCALE_STEP              = 0.001  # incremento por nivel: +0.1% por cada 5% sobre umbral


class StrategyBase(ABC):
    """Clase base abstracta para estrategias de trading."""

    def __init__(
        self,
        name: str,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols: List[str],
        timeframe: int,
        magic_number: Optional[int] = None
    ):
        """
        Inicializa la estrategia base
        
        Args:
            name: Nombre de la estrategia
            connector: Instancia de PlatformConnector
            order_manager: Instancia de OrderManager
            risk_manager: Instancia de RiskManager
            market_analyzer: Instancia de MarketAnalyzer
            symbols: Lista de símbolos a operar
            timeframe: Timeframe de la estrategia
            magic_number: Número mágico para identificar órdenes
        """
        self.name = name
        self.connector = connector
        self.order_manager = order_manager
        self.risk_manager = risk_manager
        self.market_analyzer = market_analyzer
        self.symbols = symbols
        self.timeframe = timeframe
        self.magic_number = magic_number or 234000
        self.is_running = False
        self._thread = None
        self.positions: Dict[str, Position] = {}
        self._stats = {"trades_count": 0, "wins": 0, "losses": 0}

        # Identificador único del bot: "TIPO_SIMBOLO" — coincide con trading_service
        # Se asigna desde fuera (trading_service.start_strategy) para garantizar
        # consistencia. Si no se asigna, se genera un fallback desde el nombre.
        self._strategy_id: Optional[str] = None

        # Tiempo de apertura por ticket: {ticket: datetime}
        self._position_open_times: Dict[int, datetime] = {}

        # Cooldown post-trade por simbolo: {symbol: datetime_hasta}
        self._cooldown_until: Dict[str, datetime] = {}

        # Contador de trades diarios: {symbol: {fecha_str: count}}
        self._daily_trades: Dict[str, Dict[str, int]] = {}

        # Trailing stop: SL maximo registrado por ticket {ticket: float}
        self._trailing_sl: Dict[int, float] = {}

        # Tracking de posiciones conocidas para detectar cierres por SL/TP de MT5
        # {ticket: {'symbol': str, 'type': str}}
        self._known_positions: Dict[int, Dict] = {}

        # Cache de noticias para no hacer requests en cada iteracion
        self._news_cache: List[Dict] = []
        self._news_cache_time: Optional[datetime] = None

        logger.info(f"Estrategia '{name}' inicializada para {symbols}")

    # =======================================================================
    # Metodos abstractos
    # =======================================================================

    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """Analiza el mercado y genera senales de trading."""
        pass

    @abstractmethod
    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        """Calcula precios de entrada, stop loss y take profit."""
        pass

    # =======================================================================
    # Helpers de proteccion
    # =======================================================================

    def _is_in_cooldown(self, symbol: str) -> bool:
        """True si el simbolo esta bloqueado por cooldown post-trade."""
        until = self._cooldown_until.get(symbol)
        if until and datetime.now() < until:
            remaining_min = int((until - datetime.now()).total_seconds() / 60)
            logger.info(
                f"{self.name} | {symbol}: cooldown activo — "
                f"{remaining_min} min restantes (hasta {until.strftime('%H:%M')})"
            )
            return True
        return False

    def _set_cooldown(self, symbol: str):
        """Activa cooldown post-trade para el simbolo."""
        until = datetime.now() + timedelta(hours=COOLDOWN_HOURS_AFTER_TRADE)
        self._cooldown_until[symbol] = until
        logger.info(
            f"{self.name} | {symbol}: cooldown activado "
            f"({COOLDOWN_HOURS_AFTER_TRADE}h) — sin entradas hasta {until.strftime('%H:%M')}"
        )

    def _get_daily_trade_count(self, symbol: str) -> int:
        """Devuelve cuantos trades se han abierto hoy para el simbolo."""
        today = datetime.now().strftime('%Y-%m-%d')
        return self._daily_trades.get(symbol, {}).get(today, 0)

    def _increment_daily_trade_count(self, symbol: str):
        """Incrementa el contador de trades diarios para el simbolo."""
        today = datetime.now().strftime('%Y-%m-%d')
        if symbol not in self._daily_trades:
            self._daily_trades[symbol] = {}
        self._daily_trades[symbol][today] = (
            self._daily_trades[symbol].get(today, 0) + 1
        )

    def _is_daily_limit_reached(self, symbol: str) -> bool:
        """True si ya se alcanzo el maximo de trades diarios para el simbolo."""
        count = self._get_daily_trade_count(symbol)
        if count >= MAX_DAILY_TRADES_PER_SYMBOL:
            logger.info(
                f"{self.name} | {symbol}: limite diario alcanzado "
                f"({count}/{MAX_DAILY_TRADES_PER_SYMBOL} trades hoy) — "
                f"esperando hasta manana"
            )
            return True
        return False

    def _is_trading_session(self) -> bool:
        """
        Verifica si el mercado esta en horario de sesion activa.
        Basado en UTC para ser independiente de la zona horaria del servidor.
        Solo permite operar entre SESSION_START_UTC y SESSION_END_UTC.
        Fuera de ese rango (sesion asiatica principalmente) bloquea nuevas entradas.
        Las posiciones ya abiertas NO se cierran — solo se bloquean nuevas entradas.
        """
        if not TRADING_SESSION_FILTER:
            return True

        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour

        in_session = SESSION_START_UTC <= current_hour < SESSION_END_UTC

        if not in_session:
            logger.debug(
                f"Fuera de sesion de trading: {current_hour:02d}:00 UTC "
                f"(sesion activa: {SESSION_START_UTC:02d}:00 - {SESSION_END_UTC:02d}:00 UTC)"
            )
        return in_session

    def _is_correlated_blocked(self, symbol: str, direction: str) -> bool:
        """
        Verifica si ya hay una posicion abierta en un par correlacionado
        con la misma direccion USD. Si es asi, bloquea la nueva entrada
        para evitar doble exposicion al mismo movimiento del dolar.

        Ejemplo: si EURUSD tiene un BUY abierto (apuesta a que USD baja)
        y llega señal BUY en AUDUSD (tambien apuesta a que USD baja),
        se bloquea porque es el mismo riesgo duplicado.
        """
        if not CORRELATION_FILTER:
            return False

        try:
            # Encontrar el grupo de correlacion al que pertenece el simbolo
            my_group = None
            for group in CORRELATED_GROUPS:
                if symbol in group:
                    my_group = group
                    break

            if not my_group:
                return False  # simbolo sin grupo — no aplicar filtro

            # Determinar si la direccion es "largo USD" o "corto USD"
            # Para pares XXX/USD: BUY = corto USD, SELL = largo USD
            # Para pares USD/XXX: BUY = largo USD, SELL = corto USD
            usd_is_second = symbol.endswith('USD')  # EURUSD, GBPUSD, AUDUSD
            if usd_is_second:
                usd_direction = 'SHORT' if direction == 'BUY' else 'LONG'
            else:
                usd_direction = 'LONG' if direction == 'BUY' else 'SHORT'

            # Revisar posiciones abiertas en pares del mismo grupo
            for correlated_symbol in my_group:
                if correlated_symbol == symbol:
                    continue

                positions = self.connector.get_positions(correlated_symbol)
                for pos in positions:
                    # Calcular direccion USD de la posicion existente
                    pos_usd_is_second = correlated_symbol.endswith('USD')
                    if pos_usd_is_second:
                        pos_usd_dir = 'SHORT' if pos.type == 'BUY' else 'LONG'
                    else:
                        pos_usd_dir = 'LONG' if pos.type == 'BUY' else 'SHORT'

                    if pos_usd_dir == usd_direction:
                        logger.info(
                            f"Correlacion bloqueada: {symbol} {direction} "
                            f"— ya hay {correlated_symbol} {pos.type} "
                            f"(misma exposicion USD: {usd_direction})"
                        )
                        return True

            return False

        except Exception as e:
            logger.error(f"Error en filtro de correlacion: {e}")
            return False  # en caso de error, no bloquear

    def _fetch_news_calendar(self) -> List[Dict]:
        """
        Descarga el calendario de ForexFactory y filtra eventos de alto impacto.
        Usa cache en memoria — solo hace request cada NEWS_CACHE_MINUTES minutos.
        """
        now = datetime.now(timezone.utc)
        if (self._news_cache_time and
                (now - self._news_cache_time).total_seconds() < NEWS_CACHE_MINUTES * 60):
            return self._news_cache
        try:
            req = urllib.request.Request(
                NEWS_CALENDAR_URL,
                headers={'User-Agent': 'MT5TradingBot/1.0'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode('utf-8'))
            high_impact = [
                {'title': e.get('title',''), 'country': e.get('country',''),
                 'date': e.get('date',''), 'time': e.get('time','')}
                for e in raw if e.get('impact','').lower() == 'high'
            ]
            self._news_cache      = high_impact
            self._news_cache_time = now
            logger.debug(f"Calendario actualizado: {len(high_impact)} eventos alto impacto")
            return high_impact
        except Exception as e:
            logger.warning(f"No se pudo obtener calendario de noticias: {e}")
            return self._news_cache

    def _is_news_blackout(self) -> bool:
        """
        Retorna True si estamos dentro de la ventana de blackout de una noticia
        de alto impacto (NEWS_MINUTES_BEFORE antes o NEWS_MINUTES_AFTER despues).
        En caso de error de red, permite la operacion (no bloquea).
        """
        if not NEWS_FILTER_ENABLED:
            return False
        try:
            events = self._fetch_news_calendar()
            if not events:
                return False
            now_utc = datetime.now(timezone.utc)
            for event in events:
                date_str = event.get('date', '')
                time_str = event.get('time', '')
                if not date_str or not time_str:
                    continue
                try:
                    event_dt = datetime.strptime(
                        f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                diff_min = (now_utc - event_dt).total_seconds() / 60
                if -NEWS_MINUTES_BEFORE <= diff_min <= NEWS_MINUTES_AFTER:
                    logger.info(
                        f"BLACKOUT noticia: '{event['title']}' ({event['country']}) "
                        f"{'en' if diff_min < 0 else 'hace'} {abs(int(diff_min))} min"
                    )
                    return True
            return False
        except Exception as e:
            logger.error(f"Error en filtro de noticias: {e}")
            return False

    def _is_spread_acceptable(self, symbol: str) -> bool:
        """
        Retorna False si el spread actual supera el máximo configurado para el símbolo.
        Evita entradas durante noticias de alto impacto (spread se amplía antes del
        blackout de noticias), apertura del mercado los lunes o baja liquidez.
        En caso de error obteniedo datos, permite la operación (no bloquea).
        """
        if not SPREAD_FILTER_ENABLED:
            return True
        try:
            market_data = self.connector.get_market_data(symbol)
            if not market_data:
                return True
            current_spread = market_data.spread
            max_spread = MAX_SPREAD.get(symbol, DEFAULT_MAX_SPREAD)
            if current_spread > max_spread:
                logger.info(
                    f"{self.name} | {symbol}: spread elevado {current_spread:.5f} "
                    f"> máx {max_spread:.5f} — entrada bloqueada"
                )
                return False
            return True
        except Exception as e:
            logger.debug(f"Error en filtro de spread para {symbol}: {e}")
            return True

    def _is_htf_aligned(self, symbol: str, direction: str) -> bool:
        """
        Verifica que la dirección de la señal sea coherente con la tendencia H4
        calculada mediante la pendiente de EMA50. Solo bloquea si la tendencia H4
        es claramente contraria a la señal (slope supera MTF_SLOPE_THRESHOLD).
        En mercados laterales (slope bajo el umbral) permite ambas direcciones.
        En caso de error obteniedo datos, permite la operación (no bloquea).
        """
        if not MTF_FILTER_ENABLED:
            return True
        try:
            import MetaTrader5 as mt5
            df_h4 = self.market_analyzer.get_candles(symbol, mt5.TIMEFRAME_H4, count=60)
            if df_h4 is None or len(df_h4) < 55:
                return True
            ema50 = self.market_analyzer.calculate_ema(df_h4, 50)
            if ema50 is None or ema50.isna().all():
                return True
            ema_now  = ema50.iloc[-1]
            ema_prev = ema50.iloc[-10]
            if pd.isna(ema_now) or pd.isna(ema_prev) or ema_prev == 0:
                return True
            slope = (ema_now - ema_prev) / ema_prev * 100
            if direction == 'BUY' and slope < -MTF_SLOPE_THRESHOLD:
                logger.info(
                    f"{self.name} | {symbol}: BUY bloqueado — "
                    f"tendencia H4 bajista (EMA50 slope={slope:.3f}%)"
                )
                return False
            if direction == 'SELL' and slope > MTF_SLOPE_THRESHOLD:
                logger.info(
                    f"{self.name} | {symbol}: SELL bloqueado — "
                    f"tendencia H4 alcista (EMA50 slope={slope:.3f}%)"
                )
                return False
            return True
        except Exception as e:
            logger.debug(f"Error en filtro MTF para {symbol}: {e}")
            return True

    def _get_volatility_sl_multiplier(self, symbol: str) -> float:
        """
        Calcula un multiplicador de SL basado en la volatilidad actual vs promedio.
        Retorna un factor que las estrategias aplican sobre su SL base:
          - Día normal:          retorna 1.0  (sin cambio)
          - Alta volatilidad:    retorna ATR_HIGH_VOL_SL_MULTIPLIER (ej: 1.5)
          - Baja volatilidad:    retorna ATR_LOW_VOL_SL_MULTIPLIER  (ej: 0.85)

        Uso en calculate_entry_exit de cada estrategia:
            vol_mult = self._get_volatility_sl_multiplier(symbol)
            stop_loss = entry - (atr * 2.0 * vol_mult)
        """
        if not ATR_VOLATILITY_ENABLED:
            return 1.0

        try:
            # Obtener velas D1 para calcular ATR diario promedio (20 días)
            import MetaTrader5 as mt5
            df_daily = self.market_analyzer.get_candles(symbol, mt5.TIMEFRAME_D1, count=21)
            if df_daily is None or len(df_daily) < 5:
                return 1.0

            atr_series = self.market_analyzer.calculate_atr(df_daily, period=14)
            if atr_series is None or atr_series.isna().all():
                return 1.0

            atr_current = atr_series.iloc[-1]   # ATR del día actual
            atr_avg     = atr_series.iloc[-20:-1].mean()  # promedio últimos 20 días

            if atr_avg <= 0:
                return 1.0

            ratio = atr_current / atr_avg

            if ratio >= ATR_HIGH_VOL_THRESHOLD:
                logger.info(
                    f"{symbol}: alta volatilidad detectada "
                    f"(ATR ratio: {ratio:.2f}x) — SL ampliado x{ATR_HIGH_VOL_SL_MULTIPLIER}"
                )
                return ATR_HIGH_VOL_SL_MULTIPLIER

            elif ratio <= ATR_LOW_VOL_THRESHOLD:
                logger.debug(
                    f"{symbol}: baja volatilidad "
                    f"(ATR ratio: {ratio:.2f}x) — SL reducido x{ATR_LOW_VOL_SL_MULTIPLIER}"
                )
                return ATR_LOW_VOL_SL_MULTIPLIER

            return 1.0

        except Exception as e:
            logger.error(f"Error calculando volatilidad para {symbol}: {e}")
            return 1.0  # en caso de error, usar multiplicador neutro

    def _get_scaled_risk(self) -> float:
        """
        Calcula el riesgo por trade ajustado según el rendimiento histórico
        de esta estrategia. Escala conservadoramente hacia arriba si la
        estrategia está funcionando bien, reduce si está funcionando mal.

        Retorna el porcentaje de riesgo a usar (ej: 0.01 = 1%, 0.015 = 1.5%)
        El resultado nunca supera MAX_RISK_SCALED ni baja del riesgo base.
        """
        if not AUTO_SCALE_ENABLED:
            return self.risk_manager.max_risk_per_trade

        try:
            base_risk = self.risk_manager.max_risk_per_trade
            trades    = self._stats.get('trades_count', 0)
            wins      = self._stats.get('wins', 0)

            # Sin suficientes trades — usar riesgo base
            if trades < MIN_TRADES_TO_SCALE:
                return base_risk

            win_rate = wins / trades

            # Win rate bajo umbral de reducción — usar riesgo base
            if win_rate < WIN_RATE_REDUCE:
                if self._stats.get('_scale_logged_reduce') != round(win_rate, 2):
                    logger.info(
                        f"{self.name}: riesgo en base ({base_risk*100:.1f}%) — "
                        f"WR {win_rate*100:.1f}% < umbral de reduccion {WIN_RATE_REDUCE*100:.0f}%"
                    )
                    self._stats['_scale_logged_reduce'] = round(win_rate, 2)
                return base_risk

            # Win rate bajo umbral de escalado — usar riesgo base sin escalar
            if win_rate < WIN_RATE_THRESHOLD:
                return base_risk

            # Win rate >= umbral — calcular escalado
            # Por cada 5% sobre el umbral, sumar un SCALE_STEP
            excess_pct   = win_rate - WIN_RATE_THRESHOLD
            scale_levels = int(excess_pct / 0.05)  # un nivel cada 5% de WR extra
            scaled_risk  = base_risk + (scale_levels * SCALE_STEP)
            scaled_risk  = min(scaled_risk, MAX_RISK_SCALED)

            if scaled_risk > base_risk:
                if self._stats.get('_scale_logged_up') != round(scaled_risk, 4):
                    logger.info(
                        f"{self.name}: riesgo ESCALADO a {scaled_risk*100:.1f}% "
                        f"(WR {win_rate*100:.1f}%, {trades} trades, "
                        f"base {base_risk*100:.1f}%, max {MAX_RISK_SCALED*100:.1f}%)"
                    )
                    self._stats['_scale_logged_up'] = round(scaled_risk, 4)

            return scaled_risk

        except Exception as e:
            logger.error(f"Error calculando riesgo escalado: {e}")
            return self.risk_manager.max_risk_per_trade

    def _handle_market_closed(self):
        """
        Maneja el rechazo 10018 (Market closed).
        Calcula cuanto tiempo falta para la proxima apertura del mercado
        (domingo 22:00 UTC) y aplica ese cooldown a todos los simbolos,
        evitando reintentos cada minuto durante el fin de semana.
        """
        now = datetime.now(timezone.utc)
        # Calcular proximo domingo 22:00 UTC
        # weekday(): lunes=0 ... sabado=5, domingo=6
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour >= 22:
            days_until_sunday = 7  # ya paso el domingo de esta semana
        next_open = now.replace(hour=22, minute=0, second=0, microsecond=0) + \
                    timedelta(days=days_until_sunday)

        hours_remaining = (next_open - now).total_seconds() / 3600
        logger.info(
            f"Mercado cerrado (fin de semana/feriado). "
            f"Proxima apertura estimada: domingo 22:00 UTC "
            f"({hours_remaining:.1f}h). Suspendiendo intentos."
        )
        # _cooldown_until usa datetime naive internamente — convertir
        next_open_naive = next_open.replace(tzinfo=None)
        for symbol in self.symbols:
            self._cooldown_until[symbol] = next_open_naive

    def _is_position_old_enough(self, ticket: int) -> bool:
        """True si la posicion lleva al menos MIN_POSITION_AGE_SECONDS abierta."""
        open_time = self._position_open_times.get(ticket)
        if open_time is None:
            return True  # sin registro = la consideramos suficientemente vieja
        age_seconds = (datetime.now() - open_time).total_seconds()
        if age_seconds < MIN_POSITION_AGE_SECONDS:
            remaining = int(MIN_POSITION_AGE_SECONDS - age_seconds)
            logger.info(
                f"{self.name} | Ticket {ticket}: posicion muy reciente "
                f"({int(age_seconds)}s abierta, minimo {MIN_POSITION_AGE_SECONDS}s) — "
                f"{remaining}s hasta evaluar salida"
            )
            return False
        return True

    # =======================================================================
    # Ejecucion de senales
    # =======================================================================

    def execute_signal(self, symbol: str, signal: Dict) -> bool:
        """Ejecuta una senal de trading con todas las protecciones activas."""
        try:
            # -1. Circuit breaker — bloquea el bot si acumuló demasiadas pérdidas
            if _CB_AVAILABLE:
                cb_id = self._get_circuit_breaker_id(symbol)
                allowed, cb_reason = _circuit_breaker.is_allowed(cb_id)
                if not allowed:
                    logger.warning(f"{self.name} | {symbol}: {cb_reason}")
                    return False

            # 0. Filtro de sesion — no abrir nuevas posiciones fuera de horario
            if not self._is_trading_session():
                return False

            # 0b. Filtro de correlacion — evita doble exposicion al mismo par USD
            if self._is_correlated_blocked(symbol, signal.get('direction', '')):
                return False

            # 0c. Filtro de noticias — no operar cerca de eventos de alto impacto
            if self._is_news_blackout():
                return False

            # 0d. Filtro de spread — no entrar con spread excesivo
            if not self._is_spread_acceptable(symbol):
                return False

            # 0e. Alineación multi-timeframe — solo operar en la dirección del H4
            if not self._is_htf_aligned(symbol, signal.get('direction', '')):
                return False

            # 1. Posicion ya abierta por este bot
            if self._has_open_position(symbol):
                logger.info(
                    f"Ignorando senal para {symbol}: "
                    "Ya existe una posicion abierta gestionada por este bot."
                )
                return False

            # 2. Cooldown post-trade
            if self._is_in_cooldown(symbol):
                return False

            # 3. Limite de trades diarios
            if self._is_daily_limit_reached(symbol):
                return False

            # 4. Risk manager global
            allowed, reason = self.risk_manager.is_trading_allowed()
            if not allowed:
                logger.warning(f"Trading no permitido: {reason}")
                return False

            # 5. Calcular precios
            prices = self.calculate_entry_exit(symbol, signal)

            # 6. Validar ratio R:R minimo 1:1
            rr_ratio = self.risk_manager.get_risk_reward_ratio(
                prices['entry'],
                prices['stop_loss'],
                prices['take_profit'],
                is_buy=(signal['direction'] == 'BUY')
            )
            MIN_RR_RATIO = 1.0
            if rr_ratio < MIN_RR_RATIO:
                logger.warning(
                    f"Senal rechazada para {symbol}: R:R insuficiente "
                    f"({rr_ratio:.2f} < {MIN_RR_RATIO}). "
                    f"Entry: {prices['entry']}, SL: {prices['stop_loss']}, "
                    f"TP: {prices['take_profit']}"
                )
                return False

            # 7. Calcular volumen con riesgo escalado si la estrategia lo merece
            scaled_risk = self._get_scaled_risk()
            volume = self.risk_manager.calculate_position_size(
                symbol, prices['entry'], prices['stop_loss'],
                risk_percentage=scaled_risk
            )
            if not volume:
                logger.error(f"No se pudo calcular tamanio de posicion para {symbol}")
                return False

            # 8. Crear y validar solicitud
            request = TradeRequest(
                symbol=symbol,
                order_type=OrderType.BUY if signal['direction'] == 'BUY' else OrderType.SELL,
                volume=volume,
                price=prices['entry'],
                stop_loss=prices['stop_loss'],
                take_profit=prices['take_profit'],
                magic_number=self.magic_number,
                comment=f"{self.name} {signal['direction']}"[:31]
            )

            is_valid, msg = self.risk_manager.validate_trade(request)
            if not is_valid:
                logger.warning(f"Operacion rechazada por riesgo: {msg}")
                return False

            # 9. Ejecutar
            result = self.order_manager.open_position(request)

            if result.success:
                logger.info(f"Senal ejecutada exitosamente para {symbol}")
                self.on_trade_opened(symbol, result)
                return True
            else:
                # Codigo 10018 = Market closed (fin de semana o feriado)
                # Activar cooldown global en TODOS los simbolos para no reintentar
                # cada minuto. Se calcula hasta el proximo domingo 22:00 UTC.
                if getattr(result, 'error_code', None) == 10018:
                    self._handle_market_closed()
                else:
                    logger.error(f"Error al ejecutar senal: {result.error_message}")
                return False

        except Exception as e:
            logger.error(f"Error al ejecutar senal: {str(e)}", exc_info=True)
            return False

    # =======================================================================
    # Exit conditions — sobreescribir en estrategias hijas
    # =======================================================================

    def check_exit_conditions(self, position: Position) -> bool:
        """Verifica si se deben cerrar posiciones. Base: no cierra."""
        return False

    # =======================================================================
    # Loop principal
    # =======================================================================

    def run_iteration(self) -> None:
        """Ejecuta una iteracion de la estrategia."""
        if not self.connector.is_connected():
            logger.error("No hay conexion con MT5")
            return

        for symbol in self.symbols:
            try:
                df = self.market_analyzer.get_candles(
                    symbol, self.timeframe, count=200
                )
                if df is None or df.empty:
                    logger.warning(f"No se pudieron obtener datos para {symbol}")
                    continue

                signal = self.analyze(symbol, df)
                if signal:
                    logger.info(f"Senal detectada para {symbol}: {signal}")
                    self.execute_signal(symbol, signal)

                self._check_open_positions(symbol)

            except Exception as e:
                logger.error(
                    f"Error en iteracion para {symbol}: {str(e)}", exc_info=True
                )

    def _has_open_position(self, symbol: str) -> bool:
        """Verifica si hay posicion abierta para este bot en el simbolo."""
        positions = self.connector.get_positions(symbol)
        return any(p.magic_number == self.magic_number for p in positions)

    def _update_trailing_stop(self, position) -> None:
        """
        Mueve el Stop Loss hacia la ganancia cuando el precio avanza a favor.
        Se activa cuando la ganancia supera TRAILING_ACTIVATION_ATR * ATR.
        El nuevo SL siempre es mejor que el anterior — nunca retrocede.
        """
        if not TRAILING_STOP_ENABLED:
            return

        try:
            df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=20)
            if df is None or df.empty:
                return

            atr = self.market_analyzer.calculate_atr(df).iloc[-1]
            if not atr or atr <= 0:
                return

            symbol_info = self.connector.get_symbol_info(position.symbol)
            if not symbol_info:
                return

            market_data = self.connector.get_market_data(position.symbol)
            if not market_data:
                return

            current_price = market_data.bid if position.type == "BUY" else market_data.ask
            activation_distance = TRAILING_ACTIVATION_ATR * atr
            trailing_distance   = TRAILING_STOP_ATR * atr

            if position.type == "BUY":
                profit_distance = current_price - position.price_open
                if profit_distance < activation_distance:
                    return  # ganancia insuficiente para activar trailing
                new_sl = symbol_info.normalize_price(current_price - trailing_distance)
                current_sl = position.stop_loss or 0
                if new_sl <= current_sl:
                    return  # el nuevo SL no mejora al actual
            else:
                profit_distance = position.price_open - current_price
                if profit_distance < activation_distance:
                    return
                new_sl = symbol_info.normalize_price(current_price + trailing_distance)
                current_sl = position.stop_loss or float('inf')
                if new_sl >= current_sl:
                    return

            result = self.order_manager.modify_position(position.ticket, stop_loss=new_sl)
            if result.success:
                logger.info(
                    f"Trailing Stop actualizado | {position.symbol} {position.type} "
                    f"Ticket:{position.ticket} | SL: {current_sl:.5f} -> {new_sl:.5f} "
                    f"| Precio: {current_price:.5f} | ATR: {atr:.5f}"
                )
        except Exception as e:
            logger.error(f"Error en trailing stop para ticket {position.ticket}: {e}")

    def _check_open_positions(self, symbol: str) -> None:
        """Verifica y gestiona posiciones abiertas."""
        positions = self.connector.get_positions(symbol)

        # Detectar posiciones cerradas por MT5 via SL o TP
        # Si una posicion conocida ya no aparece en get_positions, MT5 la cerro
        current_tickets = {
            p.ticket for p in positions
            if p.magic_number == self.magic_number
        }
        known_for_symbol = {
            ticket: info for ticket, info in list(self._known_positions.items())
            if info.get('symbol') == symbol
        }
        for ticket, info in known_for_symbol.items():
            if ticket not in current_tickets:
                logger.info(
                    f"{self.name} | {symbol}: posicion {ticket} cerrada por MT5 "
                    f"(SL o TP alcanzado) — enviando alerta y activando cooldown"
                )
                # Activar cooldown de re-entrada
                self._set_cooldown(symbol)
                try:
                    self.risk_manager.notify_position_closed(symbol)
                except Exception:
                    pass
                # Alerta Telegram para cierres por SL/TP
                if _TELEGRAM_AVAILABLE:
                    try:
                        # Lanzar en thread separado para no bloquear el loop principal
                        # y dar tiempo a MT5 de registrar el deal en el historial
                        _strategy_name  = self.name
                        _symbol         = symbol
                        _direction      = info.get('type', '?')
                        _ticket         = ticket

                        def _send_sltp_alert(strat, sym, direc, tkt):
                            import MetaTrader5 as mt5
                            import time as _time
                            from datetime import timedelta, datetime as _dt

                            profit = None
                            reason = 'SL/TP'

                            # Reintentar hasta 10 veces con 2 segundos entre intentos
                            # = hasta 20 segundos de espera total para que MT5 registre el deal
                            for attempt in range(10):
                                _time.sleep(2)  # esperar siempre, incluso en el primer intento
                                try:
                                    now   = _dt.now()
                                    deals = mt5.history_deals_get(
                                        now - timedelta(minutes=60), now
                                    )
                                    if not deals:
                                        continue

                                    for d in reversed(deals):
                                        if d.position_id == tkt and d.entry == 1:
                                            profit = (
                                                (d.profit      or 0.0) +
                                                (d.commission  or 0.0) +
                                                (d.swap        or 0.0)
                                            )
                                            comment = (d.comment or '').lower()
                                            if 'tp' in comment:
                                                reason = 'TP'
                                            elif 'sl' in comment or 'stop' in comment:
                                                reason = 'SL'
                                            else:
                                                reason = 'TP' if profit >= 0 else 'SL'
                                            break

                                    if profit is not None:
                                        break  # deal encontrado

                                except Exception:
                                    pass  # reintentar

                            # Si después de 20 segundos no lo encontramos,
                            # intentar obtener el profit de la posicion cerrada
                            # comparando balance antes/después (aproximado)
                            if profit is None:
                                profit = 0.0
                                logger.warning(
                                    f"No se encontro deal para ticket={tkt} "
                                    f"después de 20s — enviando con profit desconocido"
                                )

                            logger.info(
                                f"Alerta SL/TP {sym} ticket={tkt}: "
                                f"profit={profit:.2f}, reason={reason}"
                            )
                            alert_trade_closed(
                                strategy  = strat,
                                symbol    = sym,
                                direction = direc,
                                profit    = profit,
                                reason    = reason
                            )

                        import threading as _th
                        _th.Thread(
                            target=_send_sltp_alert,
                            args=(_strategy_name, _symbol, _direction, _ticket),
                            daemon=True
                        ).start()

                    except Exception as e:
                        logger.debug(f"Telegram SL/TP alert error: {e}")
                # Remover de posiciones conocidas
                self._known_positions.pop(ticket, None)

        for position in positions:
            if position.magic_number != self.magic_number:
                continue

            # Registrar como posicion conocida
            if position.ticket not in self._known_positions:
                self._known_positions[position.ticket] = {
                    'symbol': position.symbol,
                    'type':   position.type,
                }

            # Guard edad minima
            if not self._is_position_old_enough(position.ticket):
                continue

            # Trailing stop
            self._update_trailing_stop(position)

            if self.check_exit_conditions(position):
                logger.info(
                    f"Cerrando posicion {position.ticket} por condiciones de salida"
                )
                result = self.order_manager.close_position(position.ticket)
                if result.success:
                    self.on_trade_closed(position, result)

    # =======================================================================
    # Callbacks apertura / cierre
    # =======================================================================

    def on_trade_opened(self, symbol: str, result) -> None:
        """Callback cuando se abre una operacion."""
        logger.info(f"Trade abierto: {symbol} - Ticket: {result.ticket}")

        # Registrar tiempo de apertura para el guard de edad minima
        self._position_open_times[result.ticket] = datetime.now()

        # Incrementar contador diario
        self._increment_daily_trade_count(symbol)

        self._stats["trades_count"] += 1
        daily_count = self._get_daily_trade_count(symbol)
        logger.info(
            f"Estadisticas de '{self.name}' actualizadas: "
            f"Trades: {self._stats['trades_count']} | "
            f"{symbol} hoy: {daily_count}/{MAX_DAILY_TRADES_PER_SYMBOL}"
        )

        # Alerta Telegram — en thread separado para no bloquear el loop de trading.
        # Una llamada lenta a la API de Telegram (3-5s) retrasaría la siguiente
        # iteración si se hiciera síncronamente desde el hilo de la estrategia.
        if _TELEGRAM_AVAILABLE:
            try:
                positions = self.connector.get_positions(symbol)
                pos = next((p for p in positions if p.ticket == result.ticket), None)
                if pos:
                    _strategy_name = self.name
                    _symbol        = symbol
                    _pos           = pos
                    _risk_pct      = self.risk_manager.max_risk_per_trade

                    def _send_open_alert(strat, sym, position, rp):
                        try:
                            alert_trade_opened(
                                strategy  = strat,
                                symbol    = sym,
                                direction = position.type,
                                entry     = position.price_open,
                                sl        = position.stop_loss or 0,
                                tp        = position.take_profit or 0,
                                volume    = position.volume,
                                risk_pct  = rp
                            )
                        except Exception as _e:
                            logger.debug(f"Telegram open alert error: {_e}")

                    import threading as _th
                    _th.Thread(
                        target=_send_open_alert,
                        args=(_strategy_name, _symbol, _pos, _risk_pct),
                        daemon=True
                    ).start()
            except Exception as e:
                logger.debug(f"Telegram alert dispatch error: {e}")

    def on_trade_closed(self, position: Position, result) -> None:
        """Callback cuando se cierra una operacion. Activa cooldown."""
        logger.info(
            f"Trade cerrado: {position.symbol} - "
            f"Ticket: {position.ticket} - P&L: {position.profit}"
        )

        # Limpiar registro de tiempo de apertura
        self._position_open_times.pop(position.ticket, None)

        # Activar cooldown para el simbolo
        self._set_cooldown(position.symbol)

        # Notificar al risk_manager para activar cooldown anti re-entrada inmediata
        # Esto previene el bug donde MT5 cierra por SL/TP y el bot reabre al instante
        try:
            self.risk_manager.notify_position_closed(position.symbol)
        except Exception:
            pass

        # Limpiar de posiciones conocidas (evita doble alerta si el bot también detecta el cierre)
        self._known_positions.pop(position.ticket, None)

        # Alerta Telegram
        if _TELEGRAM_AVAILABLE:
            try:
                alert_trade_closed(
                    strategy  = self.name,
                    symbol    = position.symbol,
                    direction = position.type,
                    profit    = position.profit,
                )
            except Exception as e:
                logger.debug(f"Telegram alert error: {e}")

        self.update_stats(position.profit)

    def update_stats(self, profit: float) -> None:
        """Actualiza estadisticas tras cerrar una operacion."""
        if profit >= 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1

        logger.info(
            f"Estadisticas de '{self.name}' actualizadas: "
            f"Trades: {self._stats['trades_count']}, "
            f"Wins: {self._stats['wins']}, "
            f"Losses: {self._stats['losses']}"
        )

        # Persistir stats en disco para que sobrevivan reinicios
        self._save_stats()

        # Notificar al circuit breaker con el resultado del trade
        if _CB_AVAILABLE:
            try:
                # update_stats es llamado con el symbol de la posición que cerró
                # pero aquí no tenemos el symbol directamente — usamos el primero
                # de la lista. Para bots mono-símbolo (como los nuestros) es correcto.
                symbol = self.symbols[0] if self.symbols else "UNKNOWN"
                cb_id = self._get_circuit_breaker_id(symbol)
                _circuit_breaker.record_trade_result(cb_id, profit)
            except Exception as e:
                logger.debug(f"CircuitBreaker record error: {e}")

    def _get_circuit_breaker_id(self, symbol: str) -> str:
        """
        Devuelve el ID único del bot para el circuit breaker.
        Usa _strategy_id si fue asignado desde trading_service,
        o construye un fallback desde el nombre de la estrategia.
        Formato esperado: "TIPO_SIMBOLO" (ej: "BOLLINGER_GBPUSD")
        """
        if self._strategy_id:
            return self._strategy_id
        # Fallback: construir desde nombre — normalizar a mayúsculas sin espacios
        name_clean = self.name.upper().replace(" ", "_").replace("+", "").replace("-", "_")
        return f"{name_clean}_{symbol}"

    # =======================================================================
    # Persistencia de stats entre reinicios
    # =======================================================================

    def _stats_file_path(self) -> Path:
        """
        Ruta del archivo de stats para este bot.
        Usa _strategy_id si está asignado (garantiza unicidad incluso si
        dos bots tienen el mismo nombre pero distinto símbolo).
        Ejemplo: stats_MACD_XAUUSD.json, stats_EMA_CROSS_AUDUSD.json
        """
        bot_id = self._strategy_id or (
            self.name.upper()
            .replace(" ", "_")
            .replace("+", "")
            .replace("-", "_")
        )
        return Path(f"stats_{bot_id}.json")

    def _save_stats(self) -> None:
        """
        Persiste trades_count, wins y losses en disco.
        Se llama tras cada trade cerrado (update_stats).
        Solo guarda los contadores núcleo — las claves internas de logging
        (_scale_logged_up, _scale_logged_reduce) se descartan intencionalmente
        porque son flags de sesión y no tienen valor tras reiniciar.
        """
        try:
            payload = {
                "strategy_id":   self._strategy_id or self.name,
                "trades_count":  self._stats.get("trades_count", 0),
                "wins":          self._stats.get("wins", 0),
                "losses":        self._stats.get("losses", 0),
                "last_updated":  datetime.now().isoformat(),
            }
            self._stats_file_path().write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Error guardando stats de '{self.name}': {e}")

    def _load_stats(self) -> None:
        """
        Carga stats persistidas desde disco al arrancar el bot.
        Si el archivo no existe (bot nuevo o primera ejecución), conserva
        los contadores en 0 sin error — comportamiento normal.
        Si el archivo existe pero está corrupto, lo ignora y arranca limpio
        logueando el error para investigación.
        """
        path = self._stats_file_path()
        if not path.exists():
            logger.info(
                f"'{self.name}': sin stats previas — "
                f"arrancando con contadores en 0 ({path})"
            )
            return
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return
            data = json.loads(raw)
            loaded_count  = int(data.get("trades_count", 0))
            loaded_wins   = int(data.get("wins", 0))
            loaded_losses = int(data.get("losses", 0))

            # Validación mínima: wins + losses no puede superar trades_count
            if loaded_wins + loaded_losses > loaded_count:
                raise ValueError(
                    f"Stats inconsistentes: wins({loaded_wins}) + "
                    f"losses({loaded_losses}) > trades_count({loaded_count})"
                )

            self._stats["trades_count"] = loaded_count
            self._stats["wins"]         = loaded_wins
            self._stats["losses"]       = loaded_losses

            win_rate = loaded_wins / loaded_count * 100 if loaded_count > 0 else 0.0
            logger.info(
                f"'{self.name}': stats restauradas desde disco — "
                f"{loaded_count} trades | {loaded_wins}W/{loaded_losses}L | "
                f"WR {win_rate:.1f}% | archivo: {path}"
            )
        except Exception as e:
            logger.error(
                f"Error cargando stats de '{self.name}' desde {path}: {e} — "
                f"arrancando con contadores en 0"
            )

    # =======================================================================
    # Ciclo de vida del hilo
    # =======================================================================

    def start(self) -> None:
        """Inicia la estrategia en un hilo de ejecucion."""
        if self.is_running:
            logger.warning(f"Estrategia '{self.name}' ya esta en ejecucion.")
            return
        # Cargar stats persistidas antes de arrancar el hilo
        # Esto restaura trades_count/wins/losses para que el auto-escalado
        # retome desde donde quedó antes del reinicio del servidor
        self._load_stats()
        self.is_running = True
        logger.info(f"Estrategia '{self.name}' iniciada")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        """Bucle infinito — llama a run_iteration cada 60 segundos."""
        logger.info(
            f"Hilo de estrategia '{self.name}' ejecutandose en segundo plano..."
        )
        check_interval_seconds = 60

        while self.is_running:
            try:
                self.run_iteration()
            except Exception as e:
                logger.error(
                    f"Excepcion en el hilo de la estrategia '{self.name}': {str(e)}",
                    exc_info=True
                )
            for _ in range(check_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

        logger.info(f"Hilo de estrategia '{self.name}' finalizado.")

    def stop(self) -> None:
        """Detiene la estrategia y su hilo de ejecucion."""
        self.is_running = False
        if self._thread and self._thread.is_alive():
            logger.info(f"Esperando a que el hilo de '{self.name}' termine...")
        logger.info(f"Estrategia '{self.name}' detenida")

    # =======================================================================
    # Estadisticas
    # =======================================================================

    def get_daily_stats(self) -> Dict:
        """Devuelve estadisticas con contadores diarios por simbolo."""
        stats = self._stats.copy()
        stats["win_rate"] = (
            (stats["wins"] / stats["trades_count"]) * 100
            if stats["trades_count"] > 0 else 0.0
        )
        today = datetime.now().strftime('%Y-%m-%d')
        stats["daily_trades"] = {
            sym: data.get(today, 0)
            for sym, data in self._daily_trades.items()
        }
        return stats

    def get_statistics(self) -> Dict:
        """Estadisticas completas incluyendo cooldowns activos."""
        daily_stats = self.get_daily_stats()
        account_info = self.connector.get_account_info()

        return {
            'strategy_name': self.name,
            'is_running': self.is_running,
            'symbols': self.symbols,
            'daily_stats': daily_stats,
            'account_balance': account_info.balance if account_info else 0,
            'account_equity': account_info.equity if account_info else 0,
            'open_positions': len(self.connector.get_positions()),
            'cooldowns_active': {
                sym: until.strftime('%H:%M')
                for sym, until in self._cooldown_until.items()
                if datetime.now() < until
            }
        }