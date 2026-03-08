"""
Estrategia MACD Histogram Momentum
Estrategia de momentum basada en el histograma del MACD.

Lógica:
- COMPRA: Histograma MACD cambia de negativo a positivo (momentum alcista)
           + MACD cruza por encima de la línea de señal
           + Precio sobre EMA 200 (tendencia principal alcista)
- VENTA: Histograma MACD cambia de positivo a negativo (momentum bajista)
          + MACD cruza por debajo de la línea de señal
          + Precio bajo EMA 200 (tendencia principal bajista)
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class MACDStrategy(StrategyBase):
    """
    Estrategia de momentum con MACD Histogram.

    Señal de COMPRA:
    - Histograma MACD cruza de negativo a positivo
    - MACD line > Signal line
    - Precio por encima de EMA 200 (filtro de tendencia)

    Señal de VENTA:
    - Histograma MACD cruza de positivo a negativo
    - MACD line < Signal line
    - Precio por debajo de EMA 200 (filtro de tendencia)
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        ema_trend_period: int = 200,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="MACD Histogram Momentum",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.ema_trend_period = ema_trend_period

        logger.info(
            f"MACD Strategy - Fast: {fast_period}, Slow: {slow_period}, "
            f"Signal: {signal_period}, EMA Trend: {ema_trend_period}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular MACD (retorna tupla: macd_line, signal_line, histogram)
            macd_line, signal_line, histogram = self.market_analyzer.calculate_macd(
                df, self.fast_period, self.slow_period, self.signal_period
            )
            df['macd'] = macd_line
            df['signal'] = signal_line
            df['histogram'] = histogram

            # EMA de tendencia principal
            df['ema_trend'] = self.market_analyzer.calculate_ema(df, self.ema_trend_period)

            current = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['macd']) or pd.isna(current['histogram']) or
                    pd.isna(current['ema_trend'])):
                return None

            # Señal de COMPRA: histograma cruza de negativo a positivo
            if (previous['histogram'] < 0 and
                    current['histogram'] > 0 and
                    current['macd'] > current['signal'] and
                    current['close'] > current['ema_trend']):

                logger.info(
                    f"MACD BUY signal en {symbol} - "
                    f"Histogram: {current['histogram']:.6f}, MACD: {current['macd']:.6f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'MACD Histogram cruzó positivo ({current["histogram"]:.6f})',
                    'macd': current['macd'],
                    'signal': current['signal'],
                    'histogram': current['histogram'],
                    'ema_trend': current['ema_trend']
                }

            # Señal de VENTA: histograma cruza de positivo a negativo
            elif (previous['histogram'] > 0 and
                  current['histogram'] < 0 and
                  current['macd'] < current['signal'] and
                  current['close'] < current['ema_trend']):

                logger.info(
                    f"MACD SELL signal en {symbol} - "
                    f"Histogram: {current['histogram']:.6f}, MACD: {current['macd']:.6f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'MACD Histogram cruzó negativo ({current["histogram"]:.6f})',
                    'macd': current['macd'],
                    'signal': current['signal'],
                    'histogram': current['histogram'],
                    'ema_trend': current['ema_trend']
                }

            return None

        except Exception as e:
            logger.error(f"Error en MACD analyze para {symbol}: {e}", exc_info=True)
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
            stop_loss = entry - (atr * 2.0)
            take_profit = entry + (atr * 3.0)  # Ratio 1.5:1
        else:
            entry = market_data.bid
            stop_loss = entry + (atr * 2.0)
            take_profit = entry - (atr * 3.0)

        entry = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"MACD Entry: {entry}, SL: {stop_loss}, TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """Cierra si el histograma MACD revierte"""
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        if df is None or df.empty:
            return False

        _, _, histogram_s = self.market_analyzer.calculate_macd(
            df, self.fast_period, self.slow_period, self.signal_period
        )
        current_hist = histogram_s.iloc[-1]

        if pd.isna(current_hist):
            return False

        # Cerrar BUY si histograma vuelve a negativo
        if position.type == "BUY" and current_hist < 0:
            logger.info(f"Cerrando BUY - MACD Histogram negativo: {current_hist:.6f}")
            return True
        # Cerrar SELL si histograma vuelve a positivo
        elif position.type == "SELL" and current_hist > 0:
            logger.info(f"Cerrando SELL - MACD Histogram positivo: {current_hist:.6f}")
            return True

        return False
