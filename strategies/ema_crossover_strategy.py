"""
Estrategia EMA Crossover (Cruce de Medias Exponenciales)

Logica:
- COMPRA: EMA rapida cruza por encima de EMA lenta (cruce dorado)
          + Precio sobre EMA 200 (tendencia principal alcista)
          + EMA 200 en pendiente positiva (mercado en uptrend)
- VENTA: EMA rapida cruza por debajo de EMA lenta (cruce de muerte)
          + Precio bajo EMA 200 (tendencia principal bajista)
          + EMA 200 en pendiente negativa

Es una de las estrategias mas usadas en trading algoritmico profesional.
Simple, robusta, y efectiva en mercados con tendencia.

Parametros por defecto:
  - EMA rapida:  9  periodos
  - EMA lenta:  21  periodos
  - EMA tendencia: 200 periodos
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class EMACrossoverStrategy(StrategyBase):
    """
    Estrategia de cruce de EMAs con filtro de tendencia triple.
    Genera senales cuando la EMA rapida cruza la EMA lenta,
    filtradas por la EMA200 para operar solo a favor de la tendencia principal.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        fast_period: int = 9,
        slow_period: int = 21,
        trend_period: int = 200,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="EMA Crossover",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.fast_period  = fast_period
        self.slow_period  = slow_period
        self.trend_period = trend_period

        logger.info(
            f"EMA Crossover Strategy - Fast EMA: {fast_period}, "
            f"Slow EMA: {slow_period}, Trend EMA: {trend_period}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            df['ema_fast']  = self.market_analyzer.calculate_ema(df, self.fast_period)
            df['ema_slow']  = self.market_analyzer.calculate_ema(df, self.slow_period)
            df['ema_trend'] = self.market_analyzer.calculate_ema(df, self.trend_period)

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['ema_fast']) or pd.isna(current['ema_slow']) or
                    pd.isna(current['ema_trend'])):
                return None

            # Pendiente de EMA200: positiva = uptrend estructural
            ema_trend_prev = df['ema_trend'].iloc[-5]  # 5 velas atras
            ema_trend_slope_up   = current['ema_trend'] > ema_trend_prev
            ema_trend_slope_down = current['ema_trend'] < ema_trend_prev

            # Senal de COMPRA: cruce dorado (fast cruza sobre slow)
            if (previous['ema_fast'] <= previous['ema_slow'] and
                    current['ema_fast'] > current['ema_slow'] and
                    current['close'] > current['ema_trend'] and
                    ema_trend_slope_up):

                logger.info(
                    f"EMA Crossover BUY signal en {symbol} - "
                    f"EMA{self.fast_period}: {current['ema_fast']:.5f}, "
                    f"EMA{self.slow_period}: {current['ema_slow']:.5f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': (
                        f'Cruce dorado EMA{self.fast_period}/EMA{self.slow_period} '
                        f'({current["ema_fast"]:.5f} > {current["ema_slow"]:.5f})'
                    ),
                    'ema_fast':  current['ema_fast'],
                    'ema_slow':  current['ema_slow'],
                    'ema_trend': current['ema_trend']
                }

            # Senal de VENTA: cruce de muerte (fast cruza bajo slow)
            elif (previous['ema_fast'] >= previous['ema_slow'] and
                  current['ema_fast'] < current['ema_slow'] and
                  current['close'] < current['ema_trend'] and
                  ema_trend_slope_down):

                logger.info(
                    f"EMA Crossover SELL signal en {symbol} - "
                    f"EMA{self.fast_period}: {current['ema_fast']:.5f}, "
                    f"EMA{self.slow_period}: {current['ema_slow']:.5f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': (
                        f'Cruce de muerte EMA{self.fast_period}/EMA{self.slow_period} '
                        f'({current["ema_fast"]:.5f} < {current["ema_slow"]:.5f})'
                    ),
                    'ema_fast':  current['ema_fast'],
                    'ema_slow':  current['ema_slow'],
                    'ema_trend': current['ema_trend']
                }

            return None

        except Exception as e:
            logger.error(f"Error en EMA Crossover analyze para {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        df = self.market_analyzer.get_candles(symbol, self.timeframe, count=50)
        df['atr'] = self.market_analyzer.calculate_atr(df)
        atr = df['atr'].iloc[-1]

        # SL: 1.5 ATR (mas ajustado que MACD/Bollinger para mejor R:R)
        # TP: 2.5 ATR → R:R minimo 1:1.67
        if signal['direction'] == 'BUY':
            entry       = market_data.ask
            stop_loss   = entry - (atr * 1.5)
            take_profit = entry + (atr * 2.5)
        else:
            entry       = market_data.bid
            stop_loss   = entry + (atr * 1.5)
            take_profit = entry - (atr * 2.5)

        entry       = symbol_info.normalize_price(entry)
        stop_loss   = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"EMA Crossover Entry: {entry}, SL: {stop_loss}, "
            f"TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry':       entry,
            'stop_loss':   stop_loss,
            'take_profit': take_profit,
            'atr':         atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """Cierra cuando las EMAs vuelven a cruzar en sentido contrario."""
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        if df is None or df.empty:
            return False

        df['ema_fast'] = self.market_analyzer.calculate_ema(df, self.fast_period)
        df['ema_slow'] = self.market_analyzer.calculate_ema(df, self.slow_period)

        current = df.iloc[-1]

        if pd.isna(current['ema_fast']) or pd.isna(current['ema_slow']):
            return False

        # BUY: cerrar si fast vuelve a cruzar bajo slow
        if position.type == "BUY" and current['ema_fast'] < current['ema_slow']:
            logger.info(
                f"Cerrando BUY - Cruce bajista EMA: "
                f"{current['ema_fast']:.5f} < {current['ema_slow']:.5f}"
            )
            return True

        # SELL: cerrar si fast vuelve a cruzar sobre slow
        if position.type == "SELL" and current['ema_fast'] > current['ema_slow']:
            logger.info(
                f"Cerrando SELL - Cruce alcista EMA: "
                f"{current['ema_fast']:.5f} > {current['ema_slow']:.5f}"
            )
            return True

        return False
