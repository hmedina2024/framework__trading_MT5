"""
Estrategia RSI con Zonas de Sobrecompra/Sobreventa
Una de las estrategias más populares en trading algorítmico.

Lógica:
- COMPRA: RSI cruza hacia arriba desde zona de sobreventa (< 30)
- VENTA: RSI cruza hacia abajo desde zona de sobrecompra (> 70)
- Confirmación con tendencia de precio (SMA 50)
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class RSIStrategy(StrategyBase):
    """
    Estrategia basada en RSI con zonas de sobrecompra/sobreventa.

    Señal de COMPRA:
    - RSI cruza hacia arriba el nivel de sobreventa (default: 30)
    - Precio por encima de SMA 50 (tendencia alcista)

    Señal de VENTA:
    - RSI cruza hacia abajo el nivel de sobrecompra (default: 70)
    - Precio por debajo de SMA 50 (tendencia bajista)
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="RSI Oversold/Overbought",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

        logger.info(
            f"RSI Strategy - Periodo: {rsi_period}, "
            f"Sobreventa: {oversold}, Sobrecompra: {overbought}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            df['rsi'] = self.market_analyzer.calculate_rsi(df, self.rsi_period)
            df['sma50'] = self.market_analyzer.calculate_sma(df, 50)

            current = df.iloc[-1]
            previous = df.iloc[-2]

            if pd.isna(current['rsi']) or pd.isna(current['sma50']):
                return None

            # Señal de COMPRA: RSI cruza hacia arriba desde sobreventa
            if (previous['rsi'] <= self.oversold and
                    current['rsi'] > self.oversold and
                    current['close'] > current['sma50']):

                logger.info(f"RSI BUY signal en {symbol} - RSI: {current['rsi']:.2f}")
                return {
                    'direction': 'BUY',
                    'reason': f'RSI salió de sobreventa ({current["rsi"]:.2f})',
                    'rsi': current['rsi'],
                    'sma50': current['sma50']
                }

            # Señal de VENTA: RSI cruza hacia abajo desde sobrecompra
            elif (previous['rsi'] >= self.overbought and
                  current['rsi'] < self.overbought and
                  current['close'] < current['sma50']):

                logger.info(f"RSI SELL signal en {symbol} - RSI: {current['rsi']:.2f}")
                return {
                    'direction': 'SELL',
                    'reason': f'RSI salió de sobrecompra ({current["rsi"]:.2f})',
                    'rsi': current['rsi'],
                    'sma50': current['sma50']
                }

            return None

        except Exception as e:
            logger.error(f"Error en RSI analyze para {symbol}: {e}", exc_info=True)
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
            stop_loss = entry - (atr * 1.5)   # SL más ajustado (1.5 ATR)
            take_profit = entry + (atr * 2.5)  # TP a 2.5 ATR (ratio ~1.67:1)
        else:
            entry = market_data.bid
            stop_loss = entry + (atr * 1.5)
            take_profit = entry - (atr * 2.5)

        entry = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"RSI Entry: {entry}, SL: {stop_loss}, TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """Cierra si RSI llega a zona extrema contraria"""
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=30)
        if df is None or df.empty:
            return False

        df['rsi'] = self.market_analyzer.calculate_rsi(df, self.rsi_period)
        current_rsi = df['rsi'].iloc[-1]

        if pd.isna(current_rsi):
            return False

        if position.type == "BUY" and current_rsi >= self.overbought:
            logger.info(f"Cerrando BUY - RSI en sobrecompra: {current_rsi:.2f}")
            return True
        elif position.type == "SELL" and current_rsi <= self.oversold:
            logger.info(f"Cerrando SELL - RSI en sobreventa: {current_rsi:.2f}")
            return True

        return False
