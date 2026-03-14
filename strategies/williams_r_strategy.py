"""
Estrategia Williams %R + EMA Trend

Logica:
- COMPRA: Williams %R sale de zona de sobreventa (<= -80) y cruza sobre -80
          + Precio sobre EMA 50 (tendencia intermedia alcista)
          + Williams %R en la vela anterior estaba en sobreventa (<= -80)
- VENTA: Williams %R sale de zona de sobrecompra (>= -20) y cruza bajo -20
          + Precio bajo EMA 50 (tendencia intermedia bajista)
          + Williams %R en la vela anterior estaba en sobrecompra (>= -20)

Complementa perfectamente a Bollinger Bands (que ya existe en el sistema).
Bollinger detecta rebotes por volatilidad, Williams %R detecta reversiones
por momentum. Juntos capturan mas oportunidades sin duplicar senales.

Parametros por defecto:
  - Williams %R period: 14
  - EMA trend: 50 (mas sensible que EMA200 para reversiones)
  - Zona sobreventa: -80
  - Zona sobrecompra: -20
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)

# Niveles de Williams %R
OVERSOLD_LEVEL  = -80.0   # zona de sobreventa
OVERBOUGHT_LEVEL = -20.0  # zona de sobrecompra


class WilliamsRStrategy(StrategyBase):
    """
    Estrategia de reversiones con Williams %R filtrada por EMA de tendencia.
    Identifica agotamientos de precio en extremos con confirmacion de tendencia.
    Complementa a Bollinger Bands para mayor cobertura de oportunidades.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        wr_period: int = 14,
        ema_trend_period: int = 50,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="Williams %R",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.wr_period        = wr_period
        self.ema_trend_period = ema_trend_period

        logger.info(
            f"Williams %R Strategy - Period: {wr_period}, "
            f"EMA Trend: {ema_trend_period}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            df['williams_r'] = self.market_analyzer.calculate_williams_r(df, self.wr_period)
            df['ema_trend']  = self.market_analyzer.calculate_ema(df, self.ema_trend_period)

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['williams_r']) or pd.isna(previous['williams_r']) or
                    pd.isna(current['ema_trend'])):
                return None

            wr_curr = current['williams_r']
            wr_prev = previous['williams_r']

            # Senal de COMPRA: sale de sobreventa + tendencia alcista
            # Williams %R cruza de <= -80 a > -80
            if (wr_prev <= OVERSOLD_LEVEL and
                    wr_curr > OVERSOLD_LEVEL and
                    current['close'] > current['ema_trend']):

                logger.info(
                    f"Williams %R BUY signal en {symbol} - "
                    f"W%R: {wr_curr:.2f} (salio de sobreventa)"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'Williams %R salio de sobreventa ({wr_curr:.2f})',
                    'williams_r': wr_curr,
                    'ema_trend':  current['ema_trend']
                }

            # Senal de VENTA: sale de sobrecompra + tendencia bajista
            # Williams %R cruza de >= -20 a < -20
            elif (wr_prev >= OVERBOUGHT_LEVEL and
                  wr_curr < OVERBOUGHT_LEVEL and
                  current['close'] < current['ema_trend']):

                logger.info(
                    f"Williams %R SELL signal en {symbol} - "
                    f"W%R: {wr_curr:.2f} (salio de sobrecompra)"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'Williams %R salio de sobrecompra ({wr_curr:.2f})',
                    'williams_r': wr_curr,
                    'ema_trend':  current['ema_trend']
                }

            return None

        except Exception as e:
            logger.error(f"Error en Williams %R analyze para {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        df = self.market_analyzer.get_candles(symbol, self.timeframe, count=50)
        df['atr'] = self.market_analyzer.calculate_atr(df)
        atr = df['atr'].iloc[-1]

        # SL: 1.5 ATR | TP: 2.5 ATR → R:R 1:1.67
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
            f"Williams %R Entry: {entry}, SL: {stop_loss}, "
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
        """
        Cierra cuando Williams %R alcanza el extremo opuesto.
        BUY: cerrar si W%R entra en sobrecompra (>= -20)
        SELL: cerrar si W%R entra en sobreventa (<= -80)
        """
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        if df is None or df.empty:
            return False

        wr = self.market_analyzer.calculate_williams_r(df, self.wr_period)
        wr_current = wr.iloc[-1]

        if pd.isna(wr_current):
            return False

        if position.type == "BUY" and wr_current >= OVERBOUGHT_LEVEL:
            logger.info(
                f"Cerrando BUY - Williams %R en sobrecompra: {wr_current:.2f}"
            )
            return True

        if position.type == "SELL" and wr_current <= OVERSOLD_LEVEL:
            logger.info(
                f"Cerrando SELL - Williams %R en sobreventa: {wr_current:.2f}"
            )
            return True

        return False
