"""
Conector mejorado para MetaTrader5 con manejo robusto de errores
"""
import MetaTrader5 as mt5
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd

from config.settings import settings
from utils.logger import get_logger
from models.trade_models import (
    AccountInfo, Position, MarketData, SymbolInfo, TradeResult
)

logger = get_logger(__name__)

class MT5Error(Exception):
    """Excepción personalizada para errores de MT5"""
    pass

class PlatformConnector:
    """
    Conector robusto para MetaTrader5 con gestión completa de conexión,
    manejo de errores y logging detallado.
    """
    
    def __init__(self, auto_connect: bool = True):
        """
        Inicializa el conector de MT5
        
        Args:
            auto_connect: Si debe conectarse automáticamente al inicializar
        """
        self._connected = False
        self._account_info = None
        logger.info("Inicializando PlatformConnector")
        
        if auto_connect:
            self.connect()
    
    def connect(self) -> bool:
        """
        Establece conexión con MT5
        
        Returns:
            True si la conexión fue exitosa, False en caso contrario
            
        Raises:
            MT5Error: Si hay un error crítico en la conexión
        """
        if self._connected:
            logger.warning("Ya existe una conexión activa con MT5")
            return True
        
        # Validar configuración
        if not settings.validate():
            logger.error("Configuración inválida. Revisa el archivo .env")
            return False
        
        try:
            logger.info("Intentando conectar a MT5...")
            logger.debug(f"Path: {settings.MT5_PATH}")
            logger.debug(f"Server: {settings.MT5_SERVER}")
            logger.debug(f"Login: {settings.MT5_LOGIN}")
            
            # Estrategia 1: Usar terminal ya abierto, pasando credenciales sin path
            logger.info("Estrategia 1: Conectando al terminal ya abierto con credenciales...")
            initialized = mt5.initialize(
                login=settings.MT5_LOGIN,
                password=settings.MT5_PASSWORD,
                server=settings.MT5_SERVER,
                timeout=settings.MT5_TIMEOUT
            )
            
            if not initialized:
                logger.warning(f"Estrategia 1 falló: {mt5.last_error()}, intentando con path...")
                mt5.shutdown()
                
                # Estrategia 2: Lanzar nueva instancia con path completo
                logger.info("Estrategia 2: Lanzando MT5 con path completo...")
                initialized = mt5.initialize(
                    path=settings.MT5_PATH,
                    login=settings.MT5_LOGIN,
                    password=settings.MT5_PASSWORD,
                    server=settings.MT5_SERVER,
                    timeout=settings.MT5_TIMEOUT,
                    portable=settings.MT5_PORTABLE
                )
                
                if not initialized:
                    error = mt5.last_error()
                    logger.error(f"Error al inicializar MT5: {error}")
                    raise MT5Error(f"Fallo en inicialización: {error}")
            
            # Verificar conexión
            account_info = mt5.account_info()
            if account_info is None:
                error = mt5.last_error()
                logger.error(f"No se pudo obtener información de cuenta: {error}")
                mt5.shutdown()
                raise MT5Error(f"Fallo al obtener info de cuenta: {error}")
            
            self._connected = True
            self._account_info = account_info
            
            logger.info("✅ Conexión exitosa a MT5")
            logger.info(f"Cuenta: {account_info.login}")
            logger.info(f"Servidor: {account_info.server}")
            logger.info(f"Balance: {account_info.balance} {account_info.currency}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error inesperado al conectar: {str(e)}", exc_info=True)
            self._connected = False
            return False
    
    def disconnect(self) -> None:
        """Cierra la conexión con MT5"""
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("Desconectado de MT5")
        else:
            logger.warning("No hay conexión activa para cerrar")
    
    def is_connected(self) -> bool:
        """Verifica si hay conexión activa"""
        return self._connected
    
    def ensure_connection(self) -> bool:
        """
        Asegura que haya una conexión activa, reconectando si es necesario
        
        Returns:
            True si hay conexión, False en caso contrario
        """
        if not self._connected:
            logger.warning("Conexión perdida, intentando reconectar...")
            return self.connect()
        return True
    
    def get_account_info(self) -> Optional[AccountInfo]:
        """
        Obtiene información de la cuenta
        
        Returns:
            AccountInfo con los datos de la cuenta o None si hay error
        """
        if not self.ensure_connection():
            return None
        
        try:
            account = mt5.account_info()
            if account is None:
                logger.error(f"Error al obtener info de cuenta: {mt5.last_error()}")
                return None
            
            return AccountInfo(
                login=account.login,
                balance=account.balance,
                equity=account.equity,
                profit=account.profit,
                margin=account.margin,
                margin_free=account.margin_free,
                margin_level=account.margin_level if account.margin_level else 0.0,
                leverage=account.leverage,
                currency=account.currency,
                server=account.server,
                company=account.company
            )
        except Exception as e:
            logger.error(f"Error al procesar info de cuenta: {str(e)}", exc_info=True)
            return None
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Obtiene las posiciones abiertas
        
        Args:
            symbol: Filtrar por símbolo específico (opcional)
            
        Returns:
            Lista de posiciones abiertas
        """
        if not self.ensure_connection():
            return []
        
        try:
            if symbol:
                positions = mt5.positions_get(symbol=symbol)
            else:
                positions = mt5.positions_get()
            
            if positions is None:
                logger.warning(f"No se pudieron obtener posiciones: {mt5.last_error()}")
                return []
            
            result = []
            for pos in positions:
                result.append(Position(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    type="BUY" if pos.type == 0 else "SELL",
                    volume=pos.volume,
                    price_open=pos.price_open,
                    price_current=pos.price_current,
                    stop_loss=pos.sl if pos.sl > 0 else None,
                    take_profit=pos.tp if pos.tp > 0 else None,
                    profit=pos.profit,
                    swap=getattr(pos, "swap", 0.0),
                    commission=getattr(pos, "commission", 0.0),
                    magic_number=pos.magic,
                    comment=pos.comment,
                    time_open=datetime.fromtimestamp(pos.time)
                ))
            
            logger.debug(f"Obtenidas {len(result)} posiciones")
            return result
            
        except Exception as e:
            logger.error(f"Error al obtener posiciones: {str(e)}", exc_info=True)
            return []
    
    def get_market_data(self, symbol: str) -> Optional[MarketData]:
        """
        Obtiene datos de mercado actuales para un símbolo
        
        Args:
            symbol: Símbolo del instrumento
            
        Returns:
            MarketData con los datos actuales o None si hay error
        """
        if not self.ensure_connection():
            return None
        
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logger.error(f"No se pudo obtener tick para {symbol}: {mt5.last_error()}")
                return None
            
            return MarketData(
                symbol=symbol,
                bid=tick.bid,
                ask=tick.ask,
                last=tick.last,
                volume=tick.volume,
                time=datetime.fromtimestamp(tick.time),
                spread=tick.ask - tick.bid
            )
            
        except Exception as e:
            logger.error(f"Error al obtener datos de mercado: {str(e)}", exc_info=True)
            return None
    
    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """
        Obtiene información detallada de un símbolo
        
        Args:
            symbol: Símbolo del instrumento
            
        Returns:
            SymbolInfo con la información del símbolo o None si hay error
        """
        if not self.ensure_connection():
            return None
        
        try:
            info = mt5.symbol_info(symbol)
            if info is None:
                logger.error(f"No se pudo obtener info para {symbol}: {mt5.last_error()}")
                return None
            
            return SymbolInfo(
                name=info.name,
                description=info.description,
                point=info.point,
                tick_value=getattr(info, 'trade_tick_value', getattr(info, 'tick_value', 0.0)),
                digits=info.digits,
                spread=info.spread,
                trade_contract_size=info.trade_contract_size,
                volume_min=info.volume_min,
                volume_max=info.volume_max,
                volume_step=info.volume_step,
                trade_mode=info.trade_mode
            )
            
        except Exception as e:
            logger.error(f"Error al obtener info de símbolo: {str(e)}", exc_info=True)
            return None
    
    def get_available_symbols(self) -> List[str]:
        """
        Obtiene lista de símbolos disponibles
        
        Returns:
            Lista de nombres de símbolos
        """
        if not self.ensure_connection():
            return []
        
        try:
            symbols = mt5.symbols_get()
            if symbols is None:
                logger.error(f"Error al obtener símbolos: {mt5.last_error()}")
                return []
            
            return [s.name for s in symbols]
            
        except Exception as e:
            logger.error(f"Error al procesar símbolos: {str(e)}", exc_info=True)
            return []
    
    def get_historical_data(
        self,
        symbol: str,
        timeframe: int,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        count: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Obtiene datos históricos de velas
        
        Args:
            symbol: Símbolo del instrumento
            timeframe: Timeframe (usar constantes mt5.TIMEFRAME_*)
            start_date: Fecha de inicio
            end_date: Fecha de fin (opcional)
            count: Número de velas (opcional, alternativa a end_date)
            
        Returns:
            DataFrame con los datos históricos o None si hay error
        """
        if not self.ensure_connection():
            return None
        
        try:
            if count:
                rates = mt5.copy_rates_from(symbol, timeframe, start_date, count)
            elif end_date:
                rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
            else:
                logger.error("Debe especificar end_date o count")
                return None
            
            if rates is None or len(rates) == 0:
                logger.error(f"No se obtuvieron datos para {symbol}: {mt5.last_error()}")
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            
            logger.debug(f"Obtenidas {len(df)} velas para {symbol}")
            return df
            
        except Exception as e:
            logger.error(f"Error al obtener datos históricos: {str(e)}", exc_info=True)
            return None
    
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
    
    def __del__(self):
        """Destructor para asegurar desconexión"""
        if self._connected:
            self.disconnect()