"""
Estrategia MACD Histogram Momentum

Logica:
- COMPRA: Histograma MACD cambia de negativo a positivo
           + MACD cruza por encima de la linea de senal
           + Precio sobre EMA 200 (tendencia principal alcista)
           + |histograma| >= umbral minimo del simbolo (filtro de ruido)
- VENTA: Histograma MACD cambia de positivo a negativo
          + MACD cruza por debajo de la linea de senal
          + Precio bajo EMA 200 (tendencia principal bajista)
          + |histograma| >= umbral minimo del simbolo (filtro de ruido)

Correcciones vs version anterior:
  - Filtro de magnitud minima del histograma por simbolo
    Evita loops por cruces de ruido (ej: histograma oscilando +-0.000002)
  - El cooldown post-trade y el guard de edad minima estan en StrategyBase
    y se aplican automaticamente a esta y todas las estrategias
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Umbrales minimos de histograma por simbolo
# El histograma de MACD escala con el precio del instrumento:
#   GBPUSD / EURUSD / AUDUSD  (~1.x)   -> 0.0002  (100x el ruido observado)
#   USDJPY                    (~150)    -> 0.02
#   XAUUSD                    (~5000)   -> 0.5
# ---------------------------------------------------------------------------
# DEMO: umbrales reducidos al 50% para capturar mas senales y evaluar estrategia.
# Produccion: EURUSD/GBPUSD/AUDUSD=0.0002, USDJPY=0.02, XAUUSD=0.5
HISTOGRAM_MIN_THRESHOLD = {
    'EURUSD': 0.0001,
    'GBPUSD': 0.0001,
    'AUDUSD': 0.0001,
    'USDJPY': 0.01,
    'XAUUSD': 0.15,
    'USDCAD': 0.0001,
    'US30':   5.0,
    'BTCUSD': 10.0,
    'XAGUSD': 0.02,
    'DEFAULT': 0.0001,
}


class MACDStrategy(StrategyBase):
    """
    Estrategia de momentum con MACD Histogram con filtro anti-ruido.
    El cooldown post-trade y el limite diario de trades son gestionados
    por StrategyBase automaticamente.
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

    def _get_min_threshold(self, symbol: str) -> float:
        """Umbral minimo de histograma para el simbolo dado."""
        return HISTOGRAM_MIN_THRESHOLD.get(
            symbol.upper(), HISTOGRAM_MIN_THRESHOLD['DEFAULT']
        )

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular indicadores
            macd_line, signal_line, histogram = self.market_analyzer.calculate_macd(
                df, self.fast_period, self.slow_period, self.signal_period
            )
            df['macd'] = macd_line
            df['signal'] = signal_line
            df['histogram'] = histogram
            df['ema_trend'] = self.market_analyzer.calculate_ema(df, self.ema_trend_period)

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            if (pd.isna(current['macd']) or pd.isna(current['histogram']) or
                    pd.isna(current['ema_trend'])):
                return None

            # Filtro de volumen relativo: el cruce debe ocurrir con convicción
            vol_current = df['tick_volume'].iloc[-2]
            vol_avg20   = df['tick_volume'].iloc[-21:-1].mean()
            if vol_avg20 > 0 and vol_current < vol_avg20 * 0.70:
                logger.debug(
                    f"MACD {symbol}: cruce ignorado por bajo volumen "
                    f"({vol_current:.0f} < {vol_avg20 * 0.70:.0f} — 70% avg)"
                )
                return None

            # Filtro de magnitud: rechaza cruces de ruido
            min_threshold = self._get_min_threshold(symbol)
            if abs(current['histogram']) < min_threshold:
                # Loguear solo si hay cruce (para no llenar el log en lateral)
                if (previous['histogram'] < 0 < current['histogram'] or
                        previous['histogram'] > 0 > current['histogram']):
                    logger.debug(
                        f"{symbol}: cruce MACD rechazado por ruido — "
                        f"histograma {current['histogram']:.6f} < umbral {min_threshold}"
                    )
                return None

            # Filtro de divergencia RSI: bloquea cruces en agotamiento de tendencia
            rsi = self.market_analyzer.calculate_rsi(df, period=14)
            rsi_ok_buy  = True
            rsi_ok_sell = True
            if rsi is not None and not rsi.isna().iloc[-3:].any():
                price_high_now  = df['high'].iloc[-1]
                price_high_prev = df['high'].iloc[-6:-1].max()
                rsi_now         = rsi.iloc[-1]
                rsi_prev        = rsi.iloc[-6:-1].max()
                if price_high_now > price_high_prev and rsi_now < rsi_prev - 3:
                    rsi_ok_buy = False
                    logger.debug(f"MACD {symbol}: divergencia bajista RSI — BUY ignorado")

                price_low_now  = df['low'].iloc[-1]
                price_low_prev = df['low'].iloc[-6:-1].min()
                rsi_low_now    = rsi.iloc[-1]
                rsi_low_prev   = rsi.iloc[-6:-1].min()
                if price_low_now < price_low_prev and rsi_low_now > rsi_low_prev + 3:
                    rsi_ok_sell = False
                    logger.debug(f"MACD {symbol}: divergencia alcista RSI — SELL ignorado")

            # Senal de COMPRA
            if (rsi_ok_buy and
                    previous['histogram'] < 0 and
                    current['histogram'] > 0 and
                    current['macd'] > current['signal'] and
                    current['close'] > current['ema_trend']):

                logger.debug(
                    f"MACD BUY signal en {symbol} - "
                    f"Histogram: {current['histogram']:.6f}, MACD: {current['macd']:.6f}"
                )
                return {
                    'direction': 'BUY',
                    'reason': f'MACD Histogram cruzo positivo ({current["histogram"]:.6f})',
                    'macd': current['macd'],
                    'signal': current['signal'],
                    'histogram': current['histogram'],
                    'ema_trend': current['ema_trend']
                }

            # Senal de VENTA
            elif (rsi_ok_sell and
                  previous['histogram'] > 0 and
                  current['histogram'] < 0 and
                  current['macd'] < current['signal'] and
                  current['close'] < current['ema_trend']):

                logger.debug(
                    f"MACD SELL signal en {symbol} - "
                    f"Histogram: {current['histogram']:.6f}, MACD: {current['macd']:.6f}"
                )
                return {
                    'direction': 'SELL',
                    'reason': f'MACD Histogram cruzo negativo ({current["histogram"]:.6f})',
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

        # Ajuste dinámico de SL según volatilidad del día
        vol_mult = self._get_volatility_sl_multiplier(symbol)

        if signal['direction'] == 'BUY':
            entry = market_data.ask
            stop_loss   = entry - (atr * 2.0 * vol_mult)
            take_profit = entry + (atr * 3.0)
        else:
            entry = market_data.bid
            stop_loss   = entry + (atr * 2.0 * vol_mult)
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
        """
        Cierra si el histograma MACD revierte.
        El cooldown se activa automaticamente en StrategyBase.on_trade_closed().
        """
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        if df is None or df.empty:
            return False

        _, _, histogram_s = self.market_analyzer.calculate_macd(
            df, self.fast_period, self.slow_period, self.signal_period
        )
        current_hist = histogram_s.iloc[-1]

        if pd.isna(current_hist):
            return False

        if position.type == "BUY" and current_hist < 0:
            logger.info(f"Cerrando BUY - MACD Histogram negativo: {current_hist:.6f}")
            return True

        if position.type == "SELL" and current_hist > 0:
            logger.info(f"Cerrando SELL - MACD Histogram positivo: {current_hist:.6f}")
            return True

        return False