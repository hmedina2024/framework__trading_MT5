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

            # EMA200 para filtro de tendencia — solo operar a favor de la tendencia
            ema200 = self.market_analyzer.calculate_ema(df, 200)
            df['ema200'] = ema200

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['donchian_high']) or
                    pd.isna(current['atr']) or
                    pd.isna(current['atr_avg']) or
                    pd.isna(current['ema200'])):
                return None

            # Filtro 1: ATR actual debe superar el promedio (movimiento real, no ruido)
            atr_confirmed = current['atr'] > (current['atr_avg'] * self.atr_multiplier)
            if not atr_confirmed:
                return None

            # Filtro 2: el breakout debe ser de la vela ANTERIOR, no de la actual.
            # Evita entrar en mitad de una vela que puede revertir.
            # La vela anterior debe haber cerrado fuera del canal.
            prev_broke_high = previous['close'] > previous['donchian_high']
            prev_broke_low  = previous['close'] < previous['donchian_low']

            # Filtro 3: la vela actual debe confirmar — seguir en la direccion del breakout
            current_confirms_buy  = current['close'] > current['donchian_high']
            current_confirms_sell = current['close'] < current['donchian_low']

            # Filtro 4: solo operar a favor de la tendencia EMA200
            price_above_ema = current['close'] > current['ema200']
            price_below_ema = current['close'] < current['ema200']

            # Señal de COMPRA: breakout confirmado + tendencia alcista
            if prev_broke_high and current_confirms_buy and price_above_ema:
                logger.info(
                    f"BREAKOUT BUY confirmado en {symbol} - "
                    f"Precio: {current['close']:.5f} > Canal: {current['donchian_high']:.5f} "
                    f"| EMA200: {current['ema200']:.5f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'Ruptura alcista confirmada del canal ({current["donchian_high"]:.5f})',
                    'donchian_high': current['donchian_high'],
                    'donchian_low':  current['donchian_low'],
                    'atr':     current['atr'],
                    'atr_avg': current['atr_avg']
                }

            # Señal de VENTA: breakout confirmado + tendencia bajista
            elif prev_broke_low and current_confirms_sell and price_below_ema:
                logger.info(
                    f"BREAKOUT SELL confirmado en {symbol} - "
                    f"Precio: {current['close']:.5f} < Canal: {current['donchian_low']:.5f} "
                    f"| EMA200: {current['ema200']:.5f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'Ruptura bajista confirmada del canal ({current["donchian_low"]:.5f})',
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
            # SL 2x ATR debajo del canal — da espacio al precio para respirar
            # 1x ATR era demasiado ajustado, el ruido normal del mercado lo golpeaba
            stop_loss   = signal['donchian_high'] - (atr * 2.0)
            take_profit = entry + (atr * 4.0)  # R:R 1:2
        else:
            entry = market_data.bid
            stop_loss   = signal['donchian_low'] + (atr * 2.0)
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

        atr_s = self.market_analyzer.calculate_atr(df, self.atr_period).iloc[-1]

        # Cierre conservador: el precio debe regresar UN ATR completo dentro del canal
        # antes de considerar el breakout como falso. Evita cierres prematuros por ruido.
        if position.type == "BUY":
            false_breakout_level = current['donchian_high'] - atr_s
            if current['close'] < false_breakout_level:
                logger.info(
                    f"Cerrando BUY {position.ticket} - Breakout falso confirmado "
                    f"(precio {current['close']:.5f} < nivel {false_breakout_level:.5f})"
                )
                return True
        elif position.type == "SELL":
            false_breakout_level = current['donchian_low'] + atr_s
            if current['close'] > false_breakout_level:
                logger.info(
                    f"Cerrando SELL {position.ticket} - Breakout falso confirmado "
                    f"(precio {current['close']:.5f} > nivel {false_breakout_level:.5f})"
                )
                return True

        return False