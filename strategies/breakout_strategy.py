"""
Estrategia Breakout de Rango (Donchian Channel Breakout)
Estrategia de ruptura de rango, muy popular en trading de tendencias.

Lógica:
- COMPRA: Precio rompe el máximo de las últimas N velas (breakout alcista)
           + Volumen/ATR confirma la ruptura (movimiento fuerte)
- VENTA: Precio rompe el mínimo de las últimas N velas (breakout bajista)
          + ATR confirma la ruptura

Inspirada en el sistema "Turtle Trading" de Richard Dennis.
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


class BreakoutStrategy(StrategyBase):
    """
    Estrategia de ruptura de rango (Donchian Channel).

    Señal de COMPRA:
    - Precio actual supera el máximo de las últimas N velas
    - ATR actual > ATR promedio (confirma movimiento fuerte)
    - No hay posición abierta en el mismo símbolo

    Señal de VENTA:
    - Precio actual cae por debajo del mínimo de las últimas N velas
    - ATR actual > ATR promedio (confirma movimiento fuerte)
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H4,  # H4 es mejor para breakouts
        lookback_period: int = 20,    # Período del canal Donchian
        atr_period: int = 14,
        atr_multiplier: float = 1.2,  # ATR debe ser X veces el promedio
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="Donchian Breakout",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

        logger.info(
            f"Breakout Strategy - Lookback: {lookback_period} velas, "
            f"ATR: {atr_period}, Multiplicador: {atr_multiplier}"
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular canal Donchian (máximos y mínimos del período)
            # Excluimos la vela actual para evitar look-ahead bias
            df['donchian_high'] = df['high'].shift(1).rolling(self.lookback_period).max()
            df['donchian_low'] = df['low'].shift(1).rolling(self.lookback_period).min()

            # ATR para confirmar fuerza del movimiento
            df['atr'] = self.market_analyzer.calculate_atr(df, self.atr_period)
            df['atr_avg'] = df['atr'].rolling(self.atr_period * 2).mean()

            current = df.iloc[-1]

            if (pd.isna(current['donchian_high']) or
                    pd.isna(current['atr']) or
                    pd.isna(current['atr_avg'])):
                return None

            # Confirmar que el ATR actual es mayor que el promedio (movimiento fuerte)
            atr_confirmed = current['atr'] > (current['atr_avg'] * self.atr_multiplier)

            # Señal de COMPRA: precio rompe el máximo del canal
            if (current['close'] > current['donchian_high'] and atr_confirmed):

                logger.info(
                    f"BREAKOUT BUY en {symbol} - "
                    f"Precio: {current['close']:.5f} > Canal: {current['donchian_high']:.5f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'Ruptura alcista del canal ({current["donchian_high"]:.5f})',
                    'donchian_high': current['donchian_high'],
                    'donchian_low': current['donchian_low'],
                    'atr': current['atr'],
                    'atr_avg': current['atr_avg']
                }

            # Señal de VENTA: precio rompe el mínimo del canal
            elif (current['close'] < current['donchian_low'] and atr_confirmed):

                logger.info(
                    f"BREAKOUT SELL en {symbol} - "
                    f"Precio: {current['close']:.5f} < Canal: {current['donchian_low']:.5f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'Ruptura bajista del canal ({current["donchian_low"]:.5f})',
                    'donchian_high': current['donchian_high'],
                    'donchian_low': current['donchian_low'],
                    'atr': current['atr'],
                    'atr_avg': current['atr_avg']
                }

            return None

        except Exception as e:
            logger.error(f"Error en Breakout analyze para {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        atr = signal['atr']

        if signal['direction'] == 'BUY':
            entry = market_data.ask
            # SL debajo del canal (retroceso invalida el breakout)
            stop_loss = signal['donchian_high'] - (atr * 1.0)
            # TP amplio para capturar la tendencia (ratio 2:1)
            take_profit = entry + (atr * 4.0)
        else:
            entry = market_data.bid
            stop_loss = signal['donchian_low'] + (atr * 1.0)
            take_profit = entry - (atr * 4.0)

        entry = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"Breakout Entry: {entry}, SL: {stop_loss}, TP: {take_profit}, R:R=1:{rr_ratio:.2f}"
        )

        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }

    def check_exit_conditions(self, position) -> bool:
        """
        Trailing stop basado en el canal Donchian.
        Cierra si el precio regresa dentro del canal (breakout falso).
        """
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=self.lookback_period + 5)
        if df is None or df.empty:
            return False

        df['donchian_high'] = df['high'].shift(1).rolling(self.lookback_period).max()
        df['donchian_low'] = df['low'].shift(1).rolling(self.lookback_period).min()

        current = df.iloc[-1]

        if pd.isna(current['donchian_high']):
            return False

        # Si el precio regresa dentro del canal, el breakout fue falso
        if position.type == "BUY" and current['close'] < current['donchian_high']:
            logger.info(f"Cerrando BUY - Breakout falso, precio regresó al canal")
            return True
        elif position.type == "SELL" and current['close'] > current['donchian_low']:
            logger.info(f"Cerrando SELL - Breakout falso, precio regresó al canal")
            return True

        return False
