"""
Clase base para estrategias de trading
"""
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
import pandas as pd
from datetime import datetime

from utils.logger import get_logger
from models.trade_models import TradeRequest, OrderType, Position

logger = get_logger(__name__)

class StrategyBase(ABC):
    """
    Clase base abstracta para implementar estrategias de trading
    """
    
    def __init__(
        self,
        name: str,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols: List[str],
        timeframe: int,
        magic_number: Optional[int] = None
    ):
        """
        Inicializa la estrategia base
        
        Args:
            name: Nombre de la estrategia
            connector: Instancia de PlatformConnector
            order_manager: Instancia de OrderManager
            risk_manager: Instancia de RiskManager
            market_analyzer: Instancia de MarketAnalyzer
            symbols: Lista de símbolos a operar
            timeframe: Timeframe de la estrategia
            magic_number: Número mágico para identificar órdenes
        """
        self.name = name
        self.connector = connector
        self.order_manager = order_manager
        self.risk_manager = risk_manager
        self.market_analyzer = market_analyzer
        self.symbols = symbols
        self.timeframe = timeframe
        self.magic_number = magic_number or 234000
        
        self.is_running = False
        self._thread = None
        self.positions: Dict[str, Position] = {}
        self._stats = {
            "trades_count": 0,
            "wins": 0,
            "losses": 0,
        }
        
        logger.info(f"Estrategia '{name}' inicializada para {symbols}")
    
    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Analiza el mercado y genera señales de trading
        
        Args:
            symbol: Símbolo a analizar
            df: DataFrame con datos históricos
            
        Returns:
            Diccionario con señal de trading o None
        """
        pass
    
    @abstractmethod
    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        """
        Calcula precios de entrada, stop loss y take profit
        
        Args:
            symbol: Símbolo del instrumento
            signal: Señal generada por analyze()
            
        Returns:
            Diccionario con entry, stop_loss, take_profit
        """
        pass
    
    def execute_signal(self, symbol: str, signal: Dict) -> bool:
        """
        Ejecuta una señal de trading
        
        Args:
            symbol: Símbolo del instrumento
            signal: Señal a ejecutar
            
        Returns:
            True si la ejecución fue exitosa
        """
        try:
            # Verificar si ya hay posición abierta para este bot en este símbolo
            if self._has_open_position(symbol):
                logger.info(f"Ignorando señal para {symbol}: Ya existe una posición abierta gestionada por este bot.")
                return False
            
            # Verificar si se permite operar
            allowed, reason = self.risk_manager.is_trading_allowed()
            if not allowed:
                logger.warning(f"Trading no permitido: {reason}")
                return False
            
            # Calcular precios de entrada/salida
            prices = self.calculate_entry_exit(symbol, signal)
            
            # Calcular tamaño de posición
            volume = self.risk_manager.calculate_position_size(
                symbol,
                prices['entry'],
                prices['stop_loss']
            )
            
            if not volume:
                logger.error(f"No se pudo calcular tamaño de posición para {symbol}")
                return False
            
            # Crear solicitud de trading
            request = TradeRequest(
                symbol=symbol,
                order_type=OrderType.BUY if signal['direction'] == 'BUY' else OrderType.SELL,
                volume=volume,
                price=prices['entry'],
                stop_loss=prices['stop_loss'],
                take_profit=prices['take_profit'],
                magic_number=self.magic_number,
                comment=f"{self.name} {signal['direction']}"[:31]  # Truncar a 31 caracteres por seguridad
            )
            
            # Validar con gestor de riesgo
            is_valid, msg = self.risk_manager.validate_trade(request)
            if not is_valid:
                logger.warning(f"Operación rechazada por riesgo: {msg}")
                return False
            
            # Ejecutar orden
            result = self.order_manager.open_position(request)
            
            if result.success:
                logger.info(f"✅ Señal ejecutada exitosamente para {symbol}")
                self.on_trade_opened(symbol, result)
                return True
            else:
                logger.error(f"Error al ejecutar señal: {result.error_message}")
                return False
                
        except Exception as e:
            logger.error(f"Error al ejecutar señal: {str(e)}", exc_info=True)
            return False
    
    def check_exit_conditions(self, position: Position) -> bool:
        """
        Verifica si se deben cerrar posiciones abiertas
        
        Args:
            position: Posición a verificar
            
        Returns:
            True si se debe cerrar la posición
        """
        # Implementación básica - puede ser sobrescrita por estrategias específicas
        return False
    
    def run_iteration(self) -> None:
        """
        Ejecuta una iteración de la estrategia
        """
        if not self.connector.is_connected():
            logger.error("No hay conexión con MT5")
            return
        
        for symbol in self.symbols:
            try:
                # Obtener datos históricos
                df = self.market_analyzer.get_candles(
                    symbol,
                    self.timeframe,
                    count=200
                )
                
                if df is None or df.empty:
                    logger.warning(f"No se pudieron obtener datos para {symbol}")
                    continue
                
                # Analizar mercado
                signal = self.analyze(symbol, df)
                
                if signal:
                    logger.info(f"Señal detectada para {symbol}: {signal}")
                    self.execute_signal(symbol, signal)
                
                # Verificar posiciones abiertas
                self._check_open_positions(symbol)
                
            except Exception as e:
                logger.error(f"Error en iteración para {symbol}: {str(e)}", exc_info=True)
    
    def _has_open_position(self, symbol: str) -> bool:
        """Verifica si hay posición abierta para un símbolo"""
        positions = self.connector.get_positions(symbol)
        # Filtrar por magic number
        return any(p.magic_number == self.magic_number for p in positions)
    
    def _check_open_positions(self, symbol: str) -> None:
        """Verifica y gestiona posiciones abiertas"""
        positions = self.connector.get_positions(symbol)
        
        for position in positions:
            if position.magic_number != self.magic_number:
                continue
            
            # Verificar condiciones de salida
            if self.check_exit_conditions(position):
                logger.info(f"Cerrando posición {position.ticket} por condiciones de salida")
                result = self.order_manager.close_position(position.ticket)
                
                if result.success:
                    self.on_trade_closed(position, result)
    
    def on_trade_opened(self, symbol: str, result) -> None:
        """
        Callback cuando se abre una operación
        
        Args:
            symbol: Símbolo del instrumento
            result: Resultado de la operación
        """
        logger.info(f"Trade abierto: {symbol} - Ticket: {result.ticket}")
        self._stats["trades_count"] += 1
        logger.info(f"Estadísticas de '{self.name}' actualizadas: "
                   f"Trades: {self._stats['trades_count']}")
    
    def on_trade_closed(self, position: Position, result) -> None:
        """
        Callback cuando se cierra una operación
        
        Args:
            position: Posición cerrada
            result: Resultado del cierre
        """
        logger.info(f"Trade cerrado: {position.symbol} - Ticket: {position.ticket} - P&L: {position.profit}")
        self.update_stats(position.profit)
    
    def update_stats(self, profit: float) -> None:
        """Actualiza las estadísticas de la estrategia tras cerrar una operación"""
        if profit >= 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1
        
        logger.info(f"Estadísticas de '{self.name}' actualizadas: "
                   f"Trades: {self._stats['trades_count']}, "
                   f"Wins: {self._stats['wins']}, "
                   f"Losses: {self._stats['losses']}")

    def start(self) -> None:
        """Inicia la estrategia en un hilo de ejecución"""
        if self.is_running:
            logger.warning(f"Estrategia '{self.name}' ya está en ejecución.")
            return
            
        self.is_running = True
        logger.info(f"Estrategia '{self.name}' iniciada")
        
        # Iniciar el bucle principal en un hilo separado
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
    def _run_loop(self) -> None:
        """Bucle infinito que llama a run_iteration cada cierto intervalo"""
        logger.info(f"Hilo de estrategia '{self.name}' ejecutándose en segundo plano...")
        # Configurar un intervalo de verificación (por ejemplo, cada 1 minuto)
        # Esto podría ajustarse dependiendo de self.timeframe
        check_interval_seconds = 60 
        
        while self.is_running:
            try:
                self.run_iteration()
            except Exception as e:
                logger.error(f"Excepción en el hilo de la estrategia '{self.name}': {str(e)}", exc_info=True)
                
            # Dormir durante el intervalo o salir antes si is_running cambia a False
            for _ in range(check_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)
                
        logger.info(f"Hilo de estrategia '{self.name}' finalizado.")
    
    def stop(self) -> None:
        """Detiene la estrategia y su hilo de ejecución"""
        self.is_running = False
        if self._thread and self._thread.is_alive():
            logger.info(f"Esperando a que el hilo de '{self.name}' termine...")
            # En la próxima iteración del loop interno se romperá el ciclo
        logger.info(f"Estrategia '{self.name}' detenida")

    def get_daily_stats(self) -> Dict:
        """Calcula y devuelve las estadísticas de la estrategia"""
        stats = self._stats.copy()
        if stats["trades_count"] > 0:
            stats["win_rate"] = (stats["wins"] / stats["trades_count"]) * 100
        else:
            stats["win_rate"] = 0.0
        return stats
    
    def get_statistics(self) -> Dict:
        """
        Obtiene estadísticas de la estrategia
        
        Returns:
            Diccionario con estadísticas
        """
        daily_stats = self.get_daily_stats()
        account_info = self.connector.get_account_info()
        
        stats = {
            'strategy_name': self.name,
            'is_running': self.is_running,
            'symbols': self.symbols,
            'daily_stats': daily_stats,
            'account_balance': account_info.balance if account_info else 0,
            'account_equity': account_info.equity if account_info else 0,
            'open_positions': len(self.connector.get_positions())
        }
        
        return stats
