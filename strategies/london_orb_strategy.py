"""
Estrategia London Opening Range Breakout (ORB)

Logica:
  El London Open (07:00-10:00 UTC) produce el rango de precio mas importante
  del dia. Las instituciones colocan sus ordenes en ese periodo, creando
  niveles de soporte/resistencia claros. Cuando el precio rompe ese rango
  con fuerza, tiende a continuar en esa direccion durante las proximas horas.

  Fases:
    1. RANGO (07:00-10:00 UTC): calcula high/low de las 3 primeras velas H1
    2. ENTRADA (10:00-16:00 UTC): entra si el precio cierra fuera del rango
       con confirmacion de volumen (ADX > 15 — hay momentum real)
    3. SL: extremo opuesto del rango (invalida el breakout si se toca)
    4. TP: entry + 1.5x el tamanio del rango (asimetria favorable)
    5. SALIDA: si el precio regresa al interior del rango (breakout fallido)

Activos recomendados:
  - XAUUSD  → mejor activo para ORB (alta volatilidad, respeta rangos)
  - GBPUSD  → el par mas volatil en London Open
  - EURUSD  → liquido y predecible en el open
  - USDJPY  → tendencias claras con el open de Londres

Parametros:
  - ORB_RANGE_HOURS: 3 (velas H1 para formar el rango: 07-08-09 UTC)
  - ORB_TRADE_END_UTC: 16 (no entrar despues de las 16:00 UTC)
  - MIN_RANGE_PIPS: minimo de pips que debe tener el rango para ser valido
  - TP_RANGE_MULT: multiplicador del rango para el TP (default 1.5x)
  - ADX_MIN: ADX minimo para confirmar momentum en el breakout

Ajuste por simbolo:
  XAUUSD necesita MIN_RANGE_PIPS mucho mas alto que EURUSD porque su precio
  es ~2500x mas grande. Se usa un diccionario por simbolo.

Diferencia vs BREAKOUT (Donchian):
  - BREAKOUT usa canal de 20 velas en H4 — opera tendencias multi-dia
  - London ORB usa rango intradial de 3h en H1 — opera el impulso del open
  - No se solapan: BREAKOUT va en el catalogo de TRENDING_EXTREME,
    London ORB va en TRENDING_MILD y TRENDING_STRONG (igual que EMA_CROSS)
"""
import pandas as pd
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Ventana temporal (en UTC)
# ---------------------------------------------------------------------------
ORB_RANGE_START_UTC = 7    # inicio del rango: 07:00 UTC (London Open)
ORB_RANGE_END_UTC   = 10   # fin del rango: 10:00 UTC (3 velas H1)
ORB_TRADE_START_UTC = 10   # primera entrada valida: 10:00 UTC
ORB_TRADE_END_UTC   = 16   # ultima entrada: 16:00 UTC (cierre Londres)

# Hora del servidor Pepperstone: UTC+2
# 07:00 UTC = 09:00 server | 10:00 UTC = 12:00 server | 16:00 UTC = 18:00 server
SERVER_OFFSET_HOURS = 2

# ---------------------------------------------------------------------------
# Parametros por simbolo
# Los pips de XAUUSD (~2500) son ~100x los de EURUSD (~1.0)
# MIN_RANGE_PIPS: rango minimo para que el setup sea valido (evita dias planos)
# ---------------------------------------------------------------------------
SYMBOL_PARAMS = {
    'EURUSD': {'min_range': 0.0010, 'tp_mult': 1.5, 'adx_min': 15.0},  # 10 pips
    'GBPUSD': {'min_range': 0.0012, 'tp_mult': 1.5, 'adx_min': 15.0},  # 12 pips
    'USDJPY': {'min_range': 0.12,   'tp_mult': 1.5, 'adx_min': 15.0},  # 12 pips
    'XAUUSD': {'min_range': 4.0,    'tp_mult': 1.5, 'adx_min': 18.0},  # $4 rango minimo
    'AUDUSD': {'min_range': 0.0010, 'tp_mult': 1.5, 'adx_min': 15.0},  # 10 pips
    'USDCAD': {'min_range': 0.0010, 'tp_mult': 1.5, 'adx_min': 15.0},  # 10 pips
    'BTCUSD': {'min_range': 150.0,  'tp_mult': 1.5, 'adx_min': 20.0},  # $150 rango min
    'US30':   {'min_range': 80.0,   'tp_mult': 1.5, 'adx_min': 18.0},  # 80 puntos min
}
DEFAULT_PARAMS = {'min_range': 0.0010, 'tp_mult': 1.5, 'adx_min': 15.0}

# Una sola entrada por simbolo por sesion de London (reset a las 07:00 UTC)
# Se controla con _traded_today: {symbol: fecha_utc}


class LondonORBStrategy(StrategyBase):
    """
    London Opening Range Breakout en H1.
    Calcula el rango de las 3 primeras velas del London Open (07-10 UTC)
    y entra en breakout cuando el precio cierra fuera de ese rango.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="London ORB",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        # {symbol: 'YYYY-MM-DD'} — solo una entrada por dia por simbolo
        self._traded_today: Dict[str, str] = {}
        # Cache del rango calculado: {symbol: {'high': float, 'low': float, 'date': str}}
        self._orb_range: Dict[str, Dict] = {}

        logger.info(
            f"London ORB Strategy inicializada | "
            f"Rango: {ORB_RANGE_START_UTC:02d}:00-{ORB_RANGE_END_UTC:02d}:00 UTC | "
            f"Trading: {ORB_TRADE_START_UTC:02d}:00-{ORB_TRADE_END_UTC:02d}:00 UTC"
        )

    # -----------------------------------------------------------------------
    # Helpers de tiempo
    # -----------------------------------------------------------------------

    def _current_hour_utc(self) -> int:
        return datetime.now(timezone.utc).hour

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')

    def _is_in_trading_window(self) -> bool:
        """Verdadero si estamos en la ventana de entrada (10:00-16:00 UTC)."""
        h = self._current_hour_utc()
        return ORB_TRADE_START_UTC <= h < ORB_TRADE_END_UTC

    def _is_range_building(self) -> bool:
        """Verdadero si estamos en la ventana de formacion del rango (07:00-10:00 UTC)."""
        h = self._current_hour_utc()
        return ORB_RANGE_START_UTC <= h < ORB_RANGE_END_UTC

    def _already_traded_today(self, symbol: str) -> bool:
        """Verdadero si ya operamos este simbolo en la sesion London de hoy."""
        return self._traded_today.get(symbol) == self._today_utc()

    def _mark_traded(self, symbol: str) -> None:
        self._traded_today[symbol] = self._today_utc()

    # -----------------------------------------------------------------------
    # Calculo del rango ORB
    # -----------------------------------------------------------------------

    def _calculate_orb_range(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Calcula el high/low del rango ORB usando las velas H1 de 07:00-10:00 UTC.

        El DataFrame tiene timestamps en hora del servidor (UTC+2 en Pepperstone).
        Filtramos las velas cuyo 'time' cae en la ventana 09:00-12:00 server
        (= 07:00-10:00 UTC).

        Retorna {'high': float, 'low': float, 'size': float} o None si no hay
        suficientes velas del rango.
        """
        today_utc = self._today_utc()

        # Si ya calculamos el rango hoy para este simbolo, usarlo del cache
        cached = self._orb_range.get(symbol, {})
        if cached.get('date') == today_utc:
            return cached

        # Convertir timestamps del servidor a UTC restando el offset
        # df['time'] son datetime naive que representan hora del servidor (UTC+2)
        server_range_start = ORB_RANGE_START_UTC + SERVER_OFFSET_HOURS  # 9
        server_range_end   = ORB_RANGE_END_UTC   + SERVER_OFFSET_HOURS  # 12

        # Filtrar velas de hoy dentro de la ventana del rango
        # Comparamos solo la hora del servidor — ya son naives (sin tzinfo)
        range_mask = (
            df['time'].dt.hour >= server_range_start
        ) & (
            df['time'].dt.hour < server_range_end
        ) & (
            df['time'].dt.strftime('%Y-%m-%d') == today_utc
        )
        range_candles = df[range_mask]

        if len(range_candles) < 2:
            logger.debug(
                f"London ORB {symbol}: solo {len(range_candles)} vela(s) en rango "
                f"[{server_range_start}:00-{server_range_end}:00 server] — "
                f"esperando mas datos"
            )
            return None

        orb_high = float(range_candles['high'].max())
        orb_low  = float(range_candles['low'].min())
        orb_size = orb_high - orb_low

        params = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)

        if orb_size < params['min_range']:
            logger.info(
                f"London ORB {symbol}: rango insuficiente "
                f"({orb_size:.5f} < min {params['min_range']:.5f}) — "
                f"dia lateral, sin setup"
            )
            return None

        result = {
            'high': orb_high,
            'low':  orb_low,
            'size': orb_size,
            'date': today_utc,
            'candles_used': len(range_candles),
        }
        self._orb_range[symbol] = result

        logger.info(
            f"London ORB {symbol}: rango calculado | "
            f"High={orb_high:.5f} Low={orb_low:.5f} "
            f"Size={orb_size:.5f} ({len(range_candles)} velas)"
        )
        return result

    # -----------------------------------------------------------------------
    # Interfaz StrategyBase
    # -----------------------------------------------------------------------

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # 1. Solo operar durante la ventana de trading (10:00-16:00 UTC)
            if not self._is_in_trading_window():
                # Si estamos en la ventana de rango, limpiar cache del dia anterior
                if self._is_range_building():
                    if self._orb_range.get(symbol, {}).get('date') != self._today_utc():
                        self._orb_range.pop(symbol, None)
                return None

            # 2. Una sola entrada por simbolo por sesion London
            if self._already_traded_today(symbol):
                return None

            # 3. Calcular o recuperar el rango ORB
            orb = self._calculate_orb_range(symbol, df)
            if orb is None:
                return None

            # 4. Filtro ADX: confirmar que hay momentum real
            #    Sin ADX suficiente el breakout puede ser un fakeout
            params = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)
            adx = self.market_analyzer.calculate_adx(df, period=14)
            if adx is None or adx < params['adx_min']:
                logger.debug(
                    f"London ORB {symbol}: ADX={f'{adx:.1f}' if adx is not None else 'N/A'} "
                    f"< minimo {params['adx_min']} — sin momentum suficiente"
                )
                return None

            # 5. Filtro de volumen: la vela de breakout debe tener volumen superior
            #    al promedio de las últimas 20 velas. Los fakeouts suelen ocurrir
            #    en velas de bajo volumen donde no hay convicción institucional.
            vol_current = df['tick_volume'].iloc[-1]
            vol_avg20   = df['tick_volume'].iloc[-21:-1].mean()
            if vol_avg20 > 0 and vol_current < vol_avg20 * 0.80:
                logger.debug(
                    f"London ORB {symbol}: breakout ignorado por bajo volumen "
                    f"({vol_current:.0f} < {vol_avg20 * 0.80:.0f} — 80% avg)"
                )
                return None

            # 6. Detectar breakout en la vela actual (ultima vela cerrada)
            current = df.iloc[-1]
            close = float(current['close'])

            # Breakout alcista: cierre sobre el high del rango
            if close > orb['high']:
                logger.info(
                    f"London ORB BUY signal en {symbol} | "
                    f"Close={close:.5f} > ORB High={orb['high']:.5f} | "
                    f"ADX={adx:.1f} | Vol={vol_current:.0f} ({vol_current/vol_avg20*100:.0f}% avg)"
                )
                return {
                    'direction': 'BUY',
                    'reason':    f"Breakout alcista sobre ORB High ({orb['high']:.5f})",
                    'orb_high':  orb['high'],
                    'orb_low':   orb['low'],
                    'orb_size':  orb['size'],
                    'adx':       adx,
                }

            # Breakout bajista: cierre bajo el low del rango
            elif close < orb['low']:
                logger.info(
                    f"London ORB SELL signal en {symbol} | "
                    f"Close={close:.5f} < ORB Low={orb['low']:.5f} | "
                    f"ADX={adx:.1f} | Vol={vol_current:.0f} ({vol_current/vol_avg20*100:.0f}% avg)"
                )
                return {
                    'direction': 'SELL',
                    'reason':    f"Breakout bajista bajo ORB Low ({orb['low']:.5f})",
                    'orb_high':  orb['high'],
                    'orb_low':   orb['low'],
                    'orb_size':  orb['size'],
                    'adx':       adx,
                }

            return None

        except Exception as e:
            logger.error(
                f"Error en London ORB analyze para {symbol}: {e}", exc_info=True
            )
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(
                f"No se pudo obtener datos de mercado para {symbol}"
            )

        params   = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)
        orb_size = signal['orb_size']
        orb_high = signal['orb_high']
        orb_low  = signal['orb_low']

        # Ajuste dinamico de SL por volatilidad del dia (heredado de StrategyBase)
        vol_mult = self._get_volatility_sl_multiplier(symbol)

        if signal['direction'] == 'BUY':
            entry       = market_data.ask
            # SL: por debajo del low del rango (invalida el breakout)
            # Se amplía ligeramente con vol_mult en días volátiles
            stop_loss   = orb_low - (orb_size * 0.1 * vol_mult)
            # TP: entry + multiplicador del rango (asimetria favorable)
            take_profit = entry + (orb_size * params['tp_mult'])
        else:
            entry       = market_data.bid
            # SL: por encima del high del rango
            stop_loss   = orb_high + (orb_size * 0.1 * vol_mult)
            # TP: entry - multiplicador del rango
            take_profit = entry - (orb_size * params['tp_mult'])

        entry       = symbol_info.normalize_price(entry)
        stop_loss   = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"London ORB {symbol} Entry: {entry:.5f} | "
            f"SL: {stop_loss:.5f} | TP: {take_profit:.5f} | "
            f"R:R=1:{rr_ratio:.2f} | ORB size={orb_size:.5f}"
        )

        return {
            'entry':       entry,
            'stop_loss':   stop_loss,
            'take_profit': take_profit,
            'orb_size':    orb_size,
            'risk_reward': rr_ratio,
        }

    def check_exit_conditions(self, position) -> bool:
        """
        Cierra si el precio regresa más del 50% al interior del rango ORB.
        El breakout sigue siendo válido si el precio oscila cerca del nivel de
        ruptura; solo se invalida cuando regresa al punto medio del rango.
        Esto evita cierres prematuros por pequeñas retracesiones en el breakout.
        """
        orb = self._orb_range.get(position.symbol)
        if not orb or orb.get('date') != self._today_utc():
            return False

        market_data = self.connector.get_market_data(position.symbol)
        if not market_data:
            return False

        current_price = (
            market_data.bid if position.type == 'BUY' else market_data.ask
        )

        # Punto medio del rango — si el precio llega aquí el breakout fracasó
        midpoint = (orb['high'] + orb['low']) / 2

        # BUY invalidado: precio cae al interior del rango (bajo el punto medio)
        if position.type == 'BUY' and current_price < midpoint:
            logger.info(
                f"London ORB: cerrando BUY {position.symbol} — "
                f"precio ({current_price:.5f}) regreso al interior del rango "
                f"(midpoint={midpoint:.5f}, ORB High={orb['high']:.5f})"
            )
            return True

        # SELL invalidado: precio sube al interior del rango (sobre el punto medio)
        if position.type == 'SELL' and current_price > midpoint:
            logger.info(
                f"London ORB: cerrando SELL {position.symbol} — "
                f"precio ({current_price:.5f}) regreso al interior del rango "
                f"(midpoint={midpoint:.5f}, ORB Low={orb['low']:.5f})"
            )
            return True

        return False

    def on_trade_opened(self, symbol: str, result) -> None:
        """Marca el simbolo como operado hoy al abrir la posicion."""
        super().on_trade_opened(symbol, result)
        self._mark_traded(symbol)
        logger.info(
            f"London ORB {symbol}: marcado como operado hoy — "
            f"sin nuevas entradas hasta mañana 07:00 UTC"
        )
