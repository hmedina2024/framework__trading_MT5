"""
Analizador de mercado con indicadores técnicos y análisis de tendencias
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta

from utils.logger import get_logger

logger = get_logger(__name__)

class MarketAnalyzer:
    """
    Analizador de mercado con indicadores técnicos y herramientas de análisis
    """
    
    def __init__(self, connector):
        """
        Inicializa el analizador de mercado
        
        Args:
            connector: Instancia de PlatformConnector
        """
        self.connector = connector
        logger.info("MarketAnalyzer inicializado")
    
    def get_candles(
        self,
        symbol: str,
        timeframe: int,
        count: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        Obtiene velas históricas
        
        Args:
            symbol: Símbolo del instrumento
            timeframe: Timeframe (mt5.TIMEFRAME_*)
            count: Número de velas
            
        Returns:
            DataFrame con las velas o None si hay error
        """
        start_date = datetime.now()
        df = self.connector.get_historical_data(symbol, timeframe, start_date, count=count)
        
        if df is not None and not df.empty:
            logger.debug(f"Obtenidas {len(df)} velas de {symbol}")
        
        return df
    
    def calculate_sma(self, df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
        """
        Calcula Media Móvil Simple (SMA)
        
        Args:
            df: DataFrame con datos de precios
            period: Período de la media
            column: Columna a usar para el cálculo
            
        Returns:
            Serie con los valores de SMA
        """
        return df[column].rolling(window=period).mean()
    
    def calculate_ema(self, df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
        """
        Calcula Media Móvil Exponencial (EMA)
        
        Args:
            df: DataFrame con datos de precios
            period: Período de la media
            column: Columna a usar para el cálculo
            
        Returns:
            Serie con los valores de EMA
        """
        return df[column].ewm(span=period, adjust=False).mean()
    
    def calculate_rsi(self, df: pd.DataFrame, period: int = 14, column: str = 'close') -> pd.Series:
        """
        Calcula el Índice de Fuerza Relativa (RSI)
        
        Args:
            df: DataFrame con datos de precios
            period: Período del RSI
            column: Columna a usar para el cálculo
            
        Returns:
            Serie con los valores de RSI
        """
        delta = df[column].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_macd(
        self,
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        column: str = 'close'
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calcula MACD (Moving Average Convergence Divergence)
        
        Args:
            df: DataFrame con datos de precios
            fast: Período de EMA rápida
            slow: Período de EMA lenta
            signal: Período de línea de señal
            column: Columna a usar para el cálculo
            
        Returns:
            Tupla (macd_line, signal_line, histogram)
        """
        ema_fast = self.calculate_ema(df, fast, column)
        ema_slow = self.calculate_ema(df, slow, column)
        
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def calculate_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0,
        column: str = 'close'
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calcula Bandas de Bollinger
        
        Args:
            df: DataFrame con datos de precios
            period: Período de la media móvil
            std_dev: Número de desviaciones estándar
            column: Columna a usar para el cálculo
            
        Returns:
            Tupla (upper_band, middle_band, lower_band)
        """
        middle_band = self.calculate_sma(df, period, column)
        std = df[column].rolling(window=period).std()
        
        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)
        
        return upper_band, middle_band, lower_band
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calcula Average True Range (ATR)
        
        Args:
            df: DataFrame con datos de precios
            period: Período del ATR
            
        Returns:
            Serie con los valores de ATR
        """
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    def calculate_stochastic(
        self,
        df: pd.DataFrame,
        k_period: int = 14,
        d_period: int = 3
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Calcula Oscilador Estocástico
        
        Args:
            df: DataFrame con datos de precios
            k_period: Período de %K
            d_period: Período de %D
            
        Returns:
            Tupla (%K, %D)
        """
        lowest_low = df['low'].rolling(window=k_period).min()
        highest_high = df['high'].rolling(window=k_period).max()
        
        k_percent = 100 * ((df['close'] - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_period).mean()
        
        return k_percent, d_percent
    
    def detect_trend(self, df: pd.DataFrame, period: int = 20) -> str:
        """
        Detecta la tendencia del mercado
        
        Args:
            df: DataFrame con datos de precios
            period: Período para análisis de tendencia
            
        Returns:
            'UPTREND', 'DOWNTREND', o 'SIDEWAYS'
        """
        if len(df) < period:
            return 'UNKNOWN'
        
        # Calcular EMAs
        ema_short = self.calculate_ema(df, period // 2)
        ema_long = self.calculate_ema(df, period)
        
        # Obtener últimos valores
        current_short = ema_short.iloc[-1]
        current_long = ema_long.iloc[-1]
        prev_short = ema_short.iloc[-2]
        prev_long = ema_long.iloc[-2]
        
        # Determinar tendencia
        if current_short > current_long and prev_short > prev_long:
            return 'UPTREND'
        elif current_short < current_long and prev_short < prev_long:
            return 'DOWNTREND'
        else:
            return 'SIDEWAYS'
    
    def find_support_resistance(
        self,
        df: pd.DataFrame,
        window: int = 20,
        num_levels: int = 3
    ) -> Dict[str, List[float]]:
        """
        Encuentra niveles de soporte y resistencia
        
        Args:
            df: DataFrame con datos de precios
            window: Ventana para buscar máximos/mínimos locales
            num_levels: Número de niveles a identificar
            
        Returns:
            Diccionario con listas de soportes y resistencias
        """
        # Encontrar máximos locales (resistencias)
        df['resistance'] = df['high'].rolling(window=window, center=True).max()
        resistances = df[df['high'] == df['resistance']]['high'].unique()
        
        # Encontrar mínimos locales (soportes)
        df['support'] = df['low'].rolling(window=window, center=True).min()
        supports = df[df['low'] == df['support']]['low'].unique()
        
        # Ordenar y tomar los más relevantes
        resistances = sorted(resistances, reverse=True)[:num_levels]
        supports = sorted(supports)[:num_levels]
        
        return {
            'resistances': resistances.tolist(),
            'supports': supports.tolist()
        }
    
    def calculate_supertrend(
        self,
        df: pd.DataFrame,
        period: int = 10,
        multiplier: float = 3.0
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Calcula el indicador Supertrend.

        Args:
            df: DataFrame con columnas high, low, close
            period: Periodo del ATR (default 10)
            multiplier: Multiplicador del ATR (default 3.0)

        Returns:
            Tupla (supertrend_line, direction)
            direction: +1 = tendencia alcista, -1 = tendencia bajista
        """
        atr = self.calculate_atr(df, period)
        hl2 = (df['high'] + df['low']) / 2

        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)

        supertrend = pd.Series(index=df.index, dtype=float)
        direction  = pd.Series(index=df.index, dtype=int)

        for i in range(1, len(df)):
            # Banda superior
            if upper_band.iloc[i] < upper_band.iloc[i - 1] or df['close'].iloc[i - 1] > upper_band.iloc[i - 1]:
                upper_band.iloc[i] = upper_band.iloc[i]
            else:
                upper_band.iloc[i] = upper_band.iloc[i - 1]

            # Banda inferior
            if lower_band.iloc[i] > lower_band.iloc[i - 1] or df['close'].iloc[i - 1] < lower_band.iloc[i - 1]:
                lower_band.iloc[i] = lower_band.iloc[i]
            else:
                lower_band.iloc[i] = lower_band.iloc[i - 1]

            # Direccion
            if pd.isna(supertrend.iloc[i - 1]):
                direction.iloc[i] = 1
            elif supertrend.iloc[i - 1] == upper_band.iloc[i - 1]:
                direction.iloc[i] = -1 if df['close'].iloc[i] > upper_band.iloc[i] else 1
            else:
                direction.iloc[i] = 1 if df['close'].iloc[i] < lower_band.iloc[i] else -1

            supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == -1 else upper_band.iloc[i]

        return supertrend, direction

    def calculate_williams_r(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> pd.Series:
        """
        Calcula Williams %R.
        Rango: 0 a -100.
          >= -20  = zona de sobrecompra
          <= -80  = zona de sobreventa

        Args:
            df: DataFrame con columnas high, low, close
            period: Periodo de lookback (default 14)

        Returns:
            Serie con valores de Williams %R
        """
        highest_high = df['high'].rolling(window=period).max()
        lowest_low   = df['low'].rolling(window=period).min()
        williams_r   = -100 * ((highest_high - df['close']) / (highest_high - lowest_low))
        return williams_r

    def get_market_analysis(
        self,
        symbol: str,
        timeframe: int = mt5.TIMEFRAME_H1,
        count: int = 100
    ) -> Optional[Dict]:
        """
        Realiza un análisis completo del mercado
        
        Args:
            symbol: Símbolo del instrumento
            timeframe: Timeframe para análisis
            count: Número de velas a analizar
            
        Returns:
            Diccionario con análisis completo o None si hay error
        """
        logger.info(f"Analizando mercado para {symbol}")
        
        # Obtener datos
        df = self.get_candles(symbol, timeframe, count)
        if df is None or df.empty:
            logger.error(f"No se pudieron obtener datos para {symbol}")
            return None
        
        try:
            # Calcular indicadores
            df['sma_20'] = self.calculate_sma(df, 20)
            df['sma_50'] = self.calculate_sma(df, 50)
            df['ema_12'] = self.calculate_ema(df, 12)
            df['ema_26'] = self.calculate_ema(df, 26)
            df['rsi'] = self.calculate_rsi(df)
            
            macd, signal, histogram = self.calculate_macd(df)
            df['macd'] = macd
            df['macd_signal'] = signal
            df['macd_histogram'] = histogram
            
            upper_bb, middle_bb, lower_bb = self.calculate_bollinger_bands(df)
            df['bb_upper'] = upper_bb
            df['bb_middle'] = middle_bb
            df['bb_lower'] = lower_bb
            
            df['atr'] = self.calculate_atr(df)
            
            k_percent, d_percent = self.calculate_stochastic(df)
            df['stoch_k'] = k_percent
            df['stoch_d'] = d_percent
            
            # Obtener valores actuales
            current = df.iloc[-1]
            
            # Detectar tendencia
            trend = self.detect_trend(df)
            
            # Encontrar soportes y resistencias
            levels = self.find_support_resistance(df)
            
            # Obtener datos de mercado actuales
            market_data = self.connector.get_market_data(symbol)
            
            analysis = {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'current_price': market_data.bid if market_data else current['close'],
                'trend': trend,
                'indicators': {
                    'sma_20': float(current['sma_20']) if not pd.isna(current['sma_20']) else None,
                    'sma_50': float(current['sma_50']) if not pd.isna(current['sma_50']) else None,
                    'ema_12': float(current['ema_12']) if not pd.isna(current['ema_12']) else None,
                    'ema_26': float(current['ema_26']) if not pd.isna(current['ema_26']) else None,
                    'rsi': float(current['rsi']) if not pd.isna(current['rsi']) else None,
                    'macd': float(current['macd']) if not pd.isna(current['macd']) else None,
                    'macd_signal': float(current['macd_signal']) if not pd.isna(current['macd_signal']) else None,
                    'macd_histogram': float(current['macd_histogram']) if not pd.isna(current['macd_histogram']) else None,
                    'bb_upper': float(current['bb_upper']) if not pd.isna(current['bb_upper']) else None,
                    'bb_middle': float(current['bb_middle']) if not pd.isna(current['bb_middle']) else None,
                    'bb_lower': float(current['bb_lower']) if not pd.isna(current['bb_lower']) else None,
                    'atr': float(current['atr']) if not pd.isna(current['atr']) else None,
                    'stoch_k': float(current['stoch_k']) if not pd.isna(current['stoch_k']) else None,
                    'stoch_d': float(current['stoch_d']) if not pd.isna(current['stoch_d']) else None,
                },
                'levels': levels,
                'signals': self._generate_signals(df, trend)
            }
            
            logger.info(f"Análisis completado para {symbol} - Tendencia: {trend}")
            return analysis
            
        except Exception as e:
            logger.error(f"Error al analizar mercado: {str(e)}", exc_info=True)
            return None
    
    def _generate_signals(self, df: pd.DataFrame, trend: str) -> Dict[str, str]:
        """
        Genera señales de trading basadas en indicadores
        
        Args:
            df: DataFrame con indicadores calculados
            trend: Tendencia detectada
            
        Returns:
            Diccionario con señales
        """
        current = df.iloc[-1]
        signals = {}
        
        # Señal RSI
        if not pd.isna(current['rsi']):
            if current['rsi'] < 30:
                signals['rsi'] = 'OVERSOLD'
            elif current['rsi'] > 70:
                signals['rsi'] = 'OVERBOUGHT'
            else:
                signals['rsi'] = 'NEUTRAL'
        
        # Señal MACD
        if not pd.isna(current['macd']) and not pd.isna(current['macd_signal']):
            if current['macd'] > current['macd_signal']:
                signals['macd'] = 'BULLISH'
            else:
                signals['macd'] = 'BEARISH'
        
        # Señal Estocástico
        if not pd.isna(current['stoch_k']):
            if current['stoch_k'] < 20:
                signals['stochastic'] = 'OVERSOLD'
            elif current['stoch_k'] > 80:
                signals['stochastic'] = 'OVERBOUGHT'
            else:
                signals['stochastic'] = 'NEUTRAL'
        
        # Señal de tendencia
        signals['trend'] = trend
        
        # Señal general
        bullish_count = sum(1 for s in signals.values() if s in ['BULLISH', 'OVERSOLD', 'UPTREND'])
        bearish_count = sum(1 for s in signals.values() if s in ['BEARISH', 'OVERBOUGHT', 'DOWNTREND'])
        
        if bullish_count > bearish_count:
            signals['overall'] = 'BUY'
        elif bearish_count > bullish_count:
            signals['overall'] = 'SELL'
        else:
            signals['overall'] = 'NEUTRAL'
        
        return signals