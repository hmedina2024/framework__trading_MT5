"""
Estrategia Bollinger Bands Mean Reversion
Estrategia clásica de reversión a la media usando Bandas de Bollinger.

Lógica:
- COMPRA: Precio toca/cruza la banda inferior (sobreventa) + RSI < 40
- VENTA: Precio toca/cruza la banda superior (sobrecompra) + RSI > 60
- Objetivo: precio regresa a la banda media (SMA 20)
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class BollingerBandsStrategy(StrategyBase):
    """
    Estrategia de reversión a la media con Bandas de Bollinger.

    Señal de COMPRA:
    - Precio cierra por debajo de la banda inferior
    - RSI < 40 (confirmación de sobreventa)
    - Vela siguiente abre dentro de las bandas (confirmación de reversión)

    Señal de VENTA:
    - Precio cierra por encima de la banda superior
    - RSI > 60 (confirmación de sobrecompra)
    - Vela siguiente abre dentro de las bandas
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="Bollinger Bands Mean Reversion",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period

        logger.info(
            f"Bollinger Strategy - Periodo: {bb_period}, "
            f"Desviaciones: {bb_std}, RSI: {rsi_period}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular Bollinger Bands (retorna tupla: upper, middle, lower)
            bb_upper, bb_middle, bb_lower = self.market_analyzer.calculate_bollinger_bands(
                df, self.bb_period, self.bb_std
            )
            df['bb_upper'] = bb_upper
            df['bb_middle'] = bb_middle
            df['bb_lower'] = bb_lower
            df['rsi'] = self.market_analyzer.calculate_rsi(df, self.rsi_period)

            current = df.iloc[-1]
            previous = df.iloc[-2]

            if pd.isna(current['bb_upper']) or pd.isna(current['rsi']):
                return None

            # Señal de COMPRA: precio estaba bajo la banda inferior y ahora regresa
            if (previous['close'] < previous['bb_lower'] and
                    current['close'] > current['bb_lower'] and
                    current['rsi'] < 45):

                logger.info(
                    f"BB BUY signal en {symbol} - "
                    f"Precio: {current['close']:.5f}, BB Lower: {current['bb_lower']:.5f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'Rebote en banda inferior BB ({current["bb_lower"]:.5f})',
                    'bb_lower': current['bb_lower'],
                    'bb_middle': current['bb_middle'],
                    'bb_upper': current['bb_upper'],
                    'rsi': current['rsi']
                }

            # Señal de VENTA: precio estaba sobre la banda superior y ahora regresa
            elif (previous['close'] > previous['bb_upper'] and
                  current['close'] < current['bb_upper'] and
                  current['rsi'] > 55):

                logger.info(
                    f"BB SELL signal en {symbol} - "
                    f"Precio: {current['close']:.5f}, BB Upper: {current['bb_upper']:.5f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'Rechazo en banda superior BB ({current["bb_upper"]:.5f})',
                    'bb_lower': current['bb_lower'],
                    'bb_middle': current['bb_middle'],
                    'bb_upper': current['bb_upper'],
                    'rsi': current['rsi']
                }

            return None

        except Exception as e:
            logger.error(f"Error en BB analyze para {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        df = self.market_analyzer.get_candles(symbol, self.timeframe, count=50)
        df['atr'] = self.market_analyzer.calculate_atr(df)
        atr = df['atr'].iloc[-1]

        if signal['direction'] == 'BUY':
            entry = market_data.ask
            # SL justo debajo de la banda inferior
            stop_loss = signal['bb_lower'] - (atr * 0.5)
            # TP en la banda media (objetivo de reversión)
            take_profit = signal['bb_middle']
        else:
            entry = market_data.bid
            # SL justo encima de la banda superior
            stop_loss = signal['bb_upper'] + (atr * 0.5)
            # TP en la banda media
            take_profit = signal['bb_middle']

        entry = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"BB Entry: {entry}, SL: {stop_loss}, TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """Cierra si precio alcanza la banda contraria (extensión de movimiento)"""
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=30)
        if df is None or df.empty:
            return False

        bb_upper_s, _, bb_lower_s = self.market_analyzer.calculate_bollinger_bands(
            df, self.bb_period, self.bb_std
        )
        current_close = df['close'].iloc[-1]
        bb_upper = bb_upper_s.iloc[-1]
        bb_lower = bb_lower_s.iloc[-1]

        # Salir si el precio llega a la banda contraria (ganancia máxima)
        if position.type == "BUY" and current_close >= bb_upper:
            logger.info(f"Cerrando BUY - Precio alcanzó banda superior BB")
            return True
        elif position.type == "SELL" and current_close <= bb_lower:
            logger.info(f"Cerrando SELL - Precio alcanzó banda inferior BB")
            return True

        return False
