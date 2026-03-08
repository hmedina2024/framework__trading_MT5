"""
Servicio central de Trading
Encapsula la lógica de negocio y mantiene el estado del framework MT5
"""
from platform_connector import PlatformConnector
from core import OrderManager, RiskManager, MarketAnalyzer
from strategies import (
    MovingAverageCrossStrategy,
    RSIStrategy,
    BollingerBandsStrategy,
    MACDStrategy,
    BreakoutStrategy,
    StrategyBase,
)
from models import TradeRequest, TradeResult, OrderType
from utils import get_logger
import asyncio
from typing import Dict, List, Optional
import MetaTrader5 as mt5

logger = get_logger(__name__)

class TradingService:
    """
    Singleton service que gestiona la conexión MT5 y todos los componentes del framework
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TradingService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.connector = None
        self.order_manager = None
        self.risk_manager = None
        self.market_analyzer = None
        self.active_strategies: Dict[str, StrategyBase] = {}
        self._initialized = True
        
    async def initialize(self) -> bool:
        """Inicializa la conexión con MT5 y los gestores"""
        try:
            # Ejecutar conexión síncrona en un thread aparte para no bloquear
            loop = asyncio.get_event_loop()
            connected = await loop.run_in_executor(None, self._connect_sync)
            
            if connected:
                self.order_manager = OrderManager(self.connector)
                self.risk_manager = RiskManager(self.connector)
                self.market_analyzer = MarketAnalyzer(self.connector)
                logger.info("TradingService inicializado correctamente")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error inicializando TradingService: {e}")
            return False
            
    def _connect_sync(self) -> bool:
        """Conexión síncrona a MT5"""
        self.connector = PlatformConnector(auto_connect=True)
        return self.connector.is_connected()
        
    async def shutdown(self):
        """Cierra conexiones y detiene estrategias"""
        # Detener todas las estrategias
        for name, strategy in self.active_strategies.items():
            strategy.stop()
            logger.info(f"Estrategia {name} detenida")
            
        if self.connector:
            self.connector.disconnect()
            logger.info("TradingService desconectado")
            
    def is_connected(self) -> bool:
        return self.connector is not None and self.connector.is_connected()
        
    def get_account_info(self):
        if not self.is_connected():
            return None
        return self.connector.get_account_info()
        
    def get_open_positions(self):
        if not self.is_connected():
            return []
        return self.connector.get_positions()
        
    def get_open_positions_count(self) -> int:
        positions = self.get_open_positions()
        return len(positions) if positions else 0
        
    def get_active_strategies_count(self) -> int:
        return len(self.active_strategies)
        
    # --- Market Data ---
    
    def get_market_data(self, symbol: str):
        if not self.is_connected():
            return None
        return self.connector.get_market_data(symbol)
        
    def get_market_analysis(self, symbol: str, timeframe: int = mt5.TIMEFRAME_H1):
        if not self.is_connected() or not self.market_analyzer:
            return None
        return self.market_analyzer.get_market_analysis(symbol, timeframe)
        
    # --- Strategies ---
    
    # Catálogo de estrategias disponibles con sus metadatos
    STRATEGY_CATALOG = {
        "MA_CROSS": {
            "name": "MA Cross + RSI",
            "description": "Cruce de medias móviles EMA 12/26 confirmado por RSI. Clásico y confiable.",
            "timeframe": "H1",
            "class": "MovingAverageCrossStrategy",
        },
        "RSI": {
            "name": "RSI Oversold/Overbought",
            "description": "Opera rebotes desde zonas de sobreventa (<30) y sobrecompra (>70) del RSI.",
            "timeframe": "H1",
            "class": "RSIStrategy",
        },
        "BOLLINGER": {
            "name": "Bollinger Bands Mean Reversion",
            "description": "Reversión a la media cuando el precio toca las bandas de Bollinger.",
            "timeframe": "H1",
            "class": "BollingerBandsStrategy",
        },
        "MACD": {
            "name": "MACD Histogram Momentum",
            "description": "Sigue el momentum cuando el histograma MACD cambia de signo con filtro EMA 200.",
            "timeframe": "H1",
            "class": "MACDStrategy",
        },
        "BREAKOUT": {
            "name": "Donchian Breakout",
            "description": "Ruptura del canal de máximos/mínimos de 20 velas. Inspirado en Turtle Trading.",
            "timeframe": "H4",
            "class": "BreakoutStrategy",
        },
    }

    def get_strategy_catalog(self) -> list:
        """Retorna el catálogo de estrategias disponibles"""
        return [
            {"id": k, **v}
            for k, v in self.STRATEGY_CATALOG.items()
        ]

    def start_strategy(self, symbol: str, strategy_type: str = "MA_CROSS") -> bool:
        """Inicia una nueva instancia de estrategia"""
        if not self.is_connected():
            return False

        strategy_id = f"{strategy_type}_{symbol}"

        if strategy_id in self.active_strategies:
            logger.warning(f"Estrategia {strategy_id} ya está activa")
            return False

        try:
            common_args = dict(
                connector=self.connector,
                order_manager=self.order_manager,
                risk_manager=self.risk_manager,
                market_analyzer=self.market_analyzer,
                symbols=[symbol],
            )

            if strategy_type == "MA_CROSS":
                strategy = MovingAverageCrossStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "RSI":
                strategy = RSIStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "BOLLINGER":
                strategy = BollingerBandsStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "MACD":
                strategy = MACDStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "BREAKOUT":
                strategy = BreakoutStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H4
                )
            else:
                logger.error(f"Tipo de estrategia desconocida: {strategy_type}")
                return False

            strategy.start()
            self.active_strategies[strategy_id] = strategy
            logger.info(f"Estrategia {strategy_id} iniciada")
            return True

        except Exception as e:
            logger.error(f"Error al iniciar estrategia {strategy_id}: {e}")
            return False
            
    def stop_strategy(self, strategy_id: str) -> bool:
        if strategy_id in self.active_strategies:
            self.active_strategies[strategy_id].stop()
            del self.active_strategies[strategy_id]
            return True
        return False
        
    def get_strategies_status(self) -> List[Dict]:
        return [
            {
                "id": s_id,
                "name": strategy.name,
                "symbols": strategy.symbols,
                "is_running": strategy.is_running,
                "stats": strategy.get_statistics()
            }
            for s_id, strategy in self.active_strategies.items()
        ]
