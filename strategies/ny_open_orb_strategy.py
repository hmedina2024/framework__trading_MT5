"""
New York Opening Range Breakout (NY ORB)

Misma lógica que London ORB pero anclada al NY Open (13:30 UTC).
El overlap Londres+Nueva York (13:00-16:00 UTC) es el período de mayor
volumen del día — ideal para breakouts de alta convicción.

Ventanas en UTC:
  Rango:   13:00-15:00 UTC  (2 velas H1 alrededor del NY Open)
  Trading: 15:00-20:00 UTC  (desde que el precio rompe el rango hasta NY close)

Activos preferidos: US30, XAUUSD, GBPUSD, EURUSD
Diferencia vs London ORB:
  - London ORB opera el impulso europeo (07-16 UTC)
  - NY ORB opera el impulso americano (13-20 UTC)
  - Se pueden tener ambos activos simultáneamente en diferentes pares
"""
import MetaTrader5 as mt5
import pandas as pd
from typing import Optional, Dict

from strategies.london_orb_strategy import LondonORBStrategy, SYMBOL_PARAMS, DEFAULT_PARAMS
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Ventanas NY Open en UTC
# ---------------------------------------------------------------------------
NY_RANGE_START_UTC  = 13   # 13:00 UTC — pre-NY open
NY_RANGE_END_UTC    = 15   # 15:00 UTC — rango formado (2 velas H1)
NY_TRADE_START_UTC  = 15   # 15:00 UTC — primera entrada válida
NY_TRADE_END_UTC    = 20   # 20:00 UTC — cierre de sesión NY / session filter base

# Pepperstone server = UTC+2
SERVER_OFFSET_HOURS = 2


class NYOpenORBStrategy(LondonORBStrategy):
    """
    NY Opening Range Breakout.
    Hereda toda la lógica de LondonORBStrategy y sobreescribe
    únicamente los métodos relacionados con las ventanas horarias.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        magic_number=None,
    ):
        super().__init__(
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number,
        )
        self.name = "NY Open ORB"
        logger.info(
            f"NY Open ORB Strategy inicializada | "
            f"Rango: {NY_RANGE_START_UTC:02d}:00-{NY_RANGE_END_UTC:02d}:00 UTC | "
            f"Trading: {NY_TRADE_START_UTC:02d}:00-{NY_TRADE_END_UTC:02d}:00 UTC"
        )

    # -----------------------------------------------------------------------
    # Sobreescribir ventanas temporales
    # -----------------------------------------------------------------------

    def _is_in_trading_window(self) -> bool:
        h = self._current_hour_utc()
        return NY_TRADE_START_UTC <= h < NY_TRADE_END_UTC

    def _is_range_building(self) -> bool:
        h = self._current_hour_utc()
        return NY_RANGE_START_UTC <= h < NY_RANGE_END_UTC

    def _calculate_orb_range(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Calcula el rango ORB usando las velas H1 de 13:00-15:00 UTC
        (15:00-17:00 hora servidor UTC+2).
        """
        today_utc = self._today_utc()

        cached = self._orb_range.get(symbol, {})
        if cached.get('date') == today_utc:
            return cached

        server_range_start = NY_RANGE_START_UTC + SERVER_OFFSET_HOURS   # 15
        server_range_end   = NY_RANGE_END_UTC   + SERVER_OFFSET_HOURS   # 17

        range_mask = (
            df['time'].dt.hour >= server_range_start
        ) & (
            df['time'].dt.hour < server_range_end
        ) & (
            df['time'].dt.strftime('%Y-%m-%d') == today_utc
        )
        range_candles = df[range_mask]

        if len(range_candles) < 1:
            logger.debug(
                f"NY ORB {symbol}: solo {len(range_candles)} vela(s) en rango "
                f"[{server_range_start}:00-{server_range_end}:00 server] — esperando"
            )
            return None

        orb_high = float(range_candles['high'].max())
        orb_low  = float(range_candles['low'].min())
        orb_size = orb_high - orb_low

        params = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)
        if orb_size < params['min_range']:
            logger.debug(
                f"NY ORB {symbol}: rango insuficiente "
                f"({orb_size:.5f} < min {params['min_range']:.5f})"
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
            f"NY ORB {symbol}: rango calculado | "
            f"High={orb_high:.5f} Low={orb_low:.5f} "
            f"Size={orb_size:.5f} ({len(range_candles)} velas)"
        )
        return result
