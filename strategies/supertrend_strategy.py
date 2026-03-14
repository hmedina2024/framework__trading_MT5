"""
Estrategia Supertrend

Logica:
- COMPRA: Supertrend cambia a direccion alcista (direction -1 -> +1... es decir,
          precio cruza por encima de la banda superior y la linea cambia a soporte)
          + Precio sobre EMA 200 (filtro de tendencia principal)
- VENTA: Supertrend cambia a direccion bajista
          + Precio bajo EMA 200

El Supertrend es especialmente efectivo en mercados con tendencia clara
como XAUUSD y USDJPY. Genera pocas senales pero de alta calidad.

Parametros por defecto:
  - ATR period: 10
  - Multiplier: 3.0  (mayor = menos senales, mas robustas)
  - EMA trend: 200
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class SupertrendStrategy(StrategyBase):
    """
    Estrategia basada en el indicador Supertrend con filtro de tendencia EMA200.
    Ideal para mercados tendenciales. Genera senales limpias con poco ruido.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        atr_period: int = 10,
        multiplier: float = 3.0,
        ema_trend_period: int = 200,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="Supertrend",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.ema_trend_period = ema_trend_period

        logger.info(
            f"Supertrend Strategy - ATR: {atr_period}, "
            f"Multiplier: {multiplier}, EMA Trend: {ema_trend_period}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular indicadores
            supertrend, direction = self.market_analyzer.calculate_supertrend(
                df, self.atr_period, self.multiplier
            )
            df['supertrend'] = supertrend
            df['st_direction'] = direction
            df['ema_trend'] = self.market_analyzer.calculate_ema(df, self.ema_trend_period)

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['supertrend']) or pd.isna(current['st_direction']) or
                    pd.isna(current['ema_trend'])):
                return None

            curr_dir = int(current['st_direction'])
            prev_dir = int(previous['st_direction']) if not pd.isna(previous['st_direction']) else curr_dir

            # Senal de COMPRA: Supertrend cambia de bajista a alcista
            if (prev_dir == 1 and curr_dir == -1 and
                    current['close'] > current['ema_trend']):
                logger.info(
                    f"Supertrend BUY signal en {symbol} - "
                    f"Precio: {current['close']:.5f}, ST: {current['supertrend']:.5f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'Supertrend cambio a alcista ({current["supertrend"]:.5f})',
                    'supertrend': current['supertrend'],
                    'ema_trend': current['ema_trend'],
                    'st_direction': curr_dir
                }

            # Senal de VENTA: Supertrend cambia de alcista a bajista
            elif (prev_dir == -1 and curr_dir == 1 and
                  current['close'] < current['ema_trend']):
                logger.info(
                    f"Supertrend SELL signal en {symbol} - "
                    f"Precio: {current['close']:.5f}, ST: {current['supertrend']:.5f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'Supertrend cambio a bajista ({current["supertrend"]:.5f})',
                    'supertrend': current['supertrend'],
                    'ema_trend': current['ema_trend'],
                    'st_direction': curr_dir
                }

            return None

        except Exception as e:
            logger.error(f"Error en Supertrend analyze para {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        df = self.market_analyzer.get_candles(symbol, self.timeframe, count=50)
        df['atr'] = self.market_analyzer.calculate_atr(df)
        atr = df['atr'].iloc[-1]

        # SL detras de la linea Supertrend + buffer de 0.5 ATR
        supertrend_val = signal['supertrend']

        if signal['direction'] == 'BUY':
            entry = market_data.ask
            stop_loss = supertrend_val - (atr * 0.5)
            take_profit = entry + (abs(entry - stop_loss) * 2.0)  # R:R 1:2
        else:
            entry = market_data.bid
            stop_loss = supertrend_val + (atr * 0.5)
            take_profit = entry - (abs(stop_loss - entry) * 2.0)  # R:R 1:2

        entry      = symbol_info.normalize_price(entry)
        stop_loss  = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"Supertrend Entry: {entry}, SL: {stop_loss}, "
            f"TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """Cierra cuando el Supertrend revierte su direccion."""
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        if df is None or df.empty:
            return False

        _, direction = self.market_analyzer.calculate_supertrend(
            df, self.atr_period, self.multiplier
        )
        curr_dir = direction.iloc[-1]

        if pd.isna(curr_dir):
            return False

        # BUY abierto: cerrar si Supertrend se vuelve bajista (direction = 1 = precio bajo banda)
        if position.type == "BUY" and int(curr_dir) == 1:
            logger.info(f"Cerrando BUY - Supertrend revertido a bajista")
            return True

        # SELL abierto: cerrar si Supertrend se vuelve alcista (direction = -1)
        if position.type == "SELL" and int(curr_dir) == -1:
            logger.info(f"Cerrando SELL - Supertrend revertido a alcista")
            return True

        return False
