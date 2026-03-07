"""
Estrategia de ejemplo: Cruce de Medias Móviles con RSI
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)

class MovingAverageCrossStrategy(StrategyBase):
    """
    Estrategia basada en cruce de medias móviles con confirmación de RSI
    
    Señal de COMPRA:
    - EMA rápida cruza por encima de EMA lenta
    - RSI > 50 (confirmación de momentum alcista)
    
    Señal de VENTA:
    - EMA rápida cruza por debajo de EMA lenta
    - RSI < 50 (confirmación de momentum bajista)
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
        rsi_period: int = 14,
        magic_number: Optional[int] = None
    ):
        """
        Inicializa la estrategia de cruce de medias
        
        Args:
            connector: Instancia de PlatformConnector
            order_manager: Instancia de OrderManager
            risk_manager: Instancia de RiskManager
            market_analyzer: Instancia de MarketAnalyzer
            symbols: Lista de símbolos a operar
            timeframe: Timeframe de la estrategia
            fast_period: Período de EMA rápida
            slow_period: Período de EMA lenta
            rsi_period: Período del RSI
            magic_number: Número mágico
        """
        super().__init__(
            name="MA Cross + RSI",
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
        self.rsi_period = rsi_period
        
        logger.info(f"Estrategia configurada - EMA Fast: {fast_period}, "
                   f"EMA Slow: {slow_period}, RSI: {rsi_period}")
    
    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Analiza el mercado buscando cruces de medias móviles
        
        Args:
            symbol: Símbolo a analizar
            df: DataFrame con datos históricos
            
        Returns:
            Diccionario con señal o None
        """
        try:
            # Calcular indicadores
            df['ema_fast'] = self.market_analyzer.calculate_ema(df, self.fast_period)
            df['ema_slow'] = self.market_analyzer.calculate_ema(df, self.slow_period)
            df['rsi'] = self.market_analyzer.calculate_rsi(df, self.rsi_period)
            
            # Obtener valores actuales y anteriores
            current = df.iloc[-1]
            previous = df.iloc[-2]
            
            # Verificar que los indicadores estén calculados
            if pd.isna(current['ema_fast']) or pd.isna(current['ema_slow']) or pd.isna(current['rsi']):
                return None
            
            # Detectar cruce alcista
            if (previous['ema_fast'] <= previous['ema_slow'] and 
                current['ema_fast'] > current['ema_slow'] and 
                current['rsi'] > 50):
                
                logger.info(f"🔼 Señal de COMPRA detectada para {symbol}")
                return {
                    'direction': 'BUY',
                    'reason': 'EMA Cross Up + RSI > 50',
                    'ema_fast': current['ema_fast'],
                    'ema_slow': current['ema_slow'],
                    'rsi': current['rsi']
                }
            
            # Detectar cruce bajista
            elif (previous['ema_fast'] >= previous['ema_slow'] and 
                  current['ema_fast'] < current['ema_slow'] and 
                  current['rsi'] < 50):
                
                logger.info(f"🔽 Señal de VENTA detectada para {symbol}")
                return {
                    'direction': 'SELL',
                    'reason': 'EMA Cross Down + RSI < 50',
                    'ema_fast': current['ema_fast'],
                    'ema_slow': current['ema_slow'],
                    'rsi': current['rsi']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error al analizar {symbol}: {str(e)}", exc_info=True)
            return None
    
    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        """
        Calcula precios de entrada, stop loss y take profit
        
        Args:
            symbol: Símbolo del instrumento
            signal: Señal generada
            
        Returns:
            Diccionario con precios
        """
        # Obtener datos de mercado
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)
        
        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")
        
        # Obtener ATR para calcular SL/TP dinámicos
        df = self.market_analyzer.get_candles(symbol, self.timeframe, count=50)
        df['atr'] = self.market_analyzer.calculate_atr(df)
        atr = df['atr'].iloc[-1]
        
        if signal['direction'] == 'BUY':
            entry = market_data.ask
            stop_loss = entry - (atr * 2)  # SL a 2 ATR
            take_profit = entry + (atr * 3)  # TP a 3 ATR (ratio 1.5:1)
        else:  # SELL
            entry = market_data.bid
            stop_loss = entry + (atr * 2)
            take_profit = entry - (atr * 3)
        
        # Normalizar precios
        entry = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        take_profit = symbol_info.normalize_price(take_profit)
        
        # Calcular ratio riesgo/beneficio
        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit, 
            is_buy=(signal['direction'] == 'BUY')
        )
        
        logger.info(f"Precios calculados - Entry: {entry}, SL: {stop_loss}, "
                   f"TP: {take_profit}, R:R = 1:{rr_ratio:.2f}")
        
        return {
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'risk_reward': rr_ratio
        }
    
    def check_exit_conditions(self, position) -> bool:
        """
        Verifica condiciones de salida adicionales
        
        Args:
            position: Posición a verificar
            
        Returns:
            True si se debe cerrar
        """
        # Obtener datos actuales
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=50)
        
        if df is None or df.empty:
            return False
        
        # Calcular indicadores
        df['ema_fast'] = self.market_analyzer.calculate_ema(df, self.fast_period)
        df['ema_slow'] = self.market_analyzer.calculate_ema(df, self.slow_period)
        
        current = df.iloc[-1]
        
        # Cerrar si hay cruce contrario
        if position.type == "BUY" and current['ema_fast'] < current['ema_slow']:
            logger.info(f"Cerrando posición BUY por cruce bajista")
            return True
        elif position.type == "SELL" and current['ema_fast'] > current['ema_slow']:
            logger.info(f"Cerrando posición SELL por cruce alcista")
            return True
        
        return False
