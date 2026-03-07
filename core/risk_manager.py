"""
Gestor de riesgo para operaciones de trading
"""
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import math

from utils.logger import get_logger
from models.trade_models import TradeRequest, Position, AccountInfo
from config.settings import settings

logger = get_logger(__name__)

class RiskManager:
    """
    Gestor de riesgo que valida operaciones según reglas de gestión de capital
    y protección de cuenta.
    """
    
    def __init__(
        self,
        connector,
        max_risk_per_trade: Optional[float] = None,
        max_daily_loss: Optional[float] = None,
        max_open_positions: Optional[int] = None
    ):
        """
        Inicializa el gestor de riesgo
        
        Args:
            connector: Instancia de PlatformConnector
            max_risk_per_trade: Riesgo máximo por operación (% del balance)
            max_daily_loss: Pérdida máxima diaria (% del balance)
            max_open_positions: Número máximo de posiciones abiertas
        """
        self.connector = connector
        self.max_risk_per_trade = max_risk_per_trade or settings.MAX_RISK_PER_TRADE
        self.max_daily_loss = max_daily_loss or settings.MAX_DAILY_LOSS
        self.max_open_positions = max_open_positions or settings.MAX_OPEN_POSITIONS
        
        self._daily_stats = {
            "date": datetime.now().date(),
            "starting_balance": 0.0,
            "trades_count": 0,
            "wins": 0,
            "losses": 0,
            "total_profit": 0.0
        }
        
        logger.info(f"RiskManager inicializado - Riesgo/Trade: {self.max_risk_per_trade*100}%, "
                   f"Pérdida diaria máx: {self.max_daily_loss*100}%, "
                   f"Posiciones máx: {self.max_open_positions}")
    
    def _reset_daily_stats_if_needed(self, account_info: AccountInfo) -> None:
        """Reinicia estadísticas diarias si es un nuevo día"""
        today = datetime.now().date()
        if self._daily_stats["date"] != today:
            logger.info("Nuevo día de trading - Reiniciando estadísticas")
            self._daily_stats = {
                "date": today,
                "starting_balance": account_info.balance,
                "trades_count": 0,
                "wins": 0,
                "losses": 0,
                "total_profit": 0.0
            }
    
    def validate_trade(self, request: TradeRequest) -> tuple[bool, str]:
        """
        Valida si una operación cumple con las reglas de riesgo
        
        Args:
            request: Solicitud de trading a validar
            
        Returns:
            Tupla (es_válido, mensaje)
        """
        # Obtener información de cuenta
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"
        
        # Resetear estadísticas si es necesario
        self._reset_daily_stats_if_needed(account_info)
        
        # Verificar pérdida diaria máxima
        if not self._check_daily_loss_limit(account_info):
            return False, f"Límite de pérdida diaria alcanzado ({self.max_daily_loss*100}%)"
        
        # Verificar número máximo de posiciones
        if not self._check_max_positions():
            return False, f"Número máximo de posiciones alcanzado ({self.max_open_positions})"
        
        # Verificar margen disponible
        if not self._check_margin_available(account_info, request):
            return False, "Margen insuficiente para la operación"
        
        # Verificar riesgo por operación
        if request.stop_loss:
            if not self._check_risk_per_trade(account_info, request):
                return False, f"Riesgo por operación excede el límite ({self.max_risk_per_trade*100}%)"
        
        logger.info(f"✅ Validación de riesgo aprobada para {request.symbol}")
        return True, "Operación aprobada"
    
    def _check_daily_loss_limit(self, account_info: AccountInfo) -> bool:
        """
        Verifica si se ha alcanzado el límite de pérdida diaria
        
        Args:
            account_info: Información de la cuenta
            
        Returns:
            True si está dentro del límite
        """
        if self._daily_stats["starting_balance"] == 0:
            self._daily_stats["starting_balance"] = account_info.balance
        
        starting_balance = self._daily_stats["starting_balance"]
        current_balance = account_info.balance
        
        loss = starting_balance - current_balance
        loss_percentage = loss / starting_balance if starting_balance > 0 else 0
        
        if loss_percentage >= self.max_daily_loss:
            logger.warning(f"⚠️ Límite de pérdida diaria alcanzado: {loss_percentage*100:.2f}%")
            return False
        
        return True
    
    def _check_max_positions(self) -> bool:
        """
        Verifica si se ha alcanzado el número máximo de posiciones
        
        Returns:
            True si está dentro del límite
        """
        positions = self.connector.get_positions()
        current_positions = len(positions)
        
        if current_positions >= self.max_open_positions:
            logger.warning(f"⚠️ Número máximo de posiciones alcanzado: {current_positions}/{self.max_open_positions}")
            return False
        
        return True
    
    def _check_margin_available(self, account_info: AccountInfo, request: TradeRequest) -> bool:
        """
        Verifica si hay margen suficiente para la operación
        
        Args:
            account_info: Información de la cuenta
            request: Solicitud de trading
            
        Returns:
            True si hay margen suficiente
        """
        # Obtener información del símbolo
        symbol_info = self.connector.get_symbol_info(request.symbol)
        if not symbol_info:
            logger.warning(f"No se pudo obtener info de {request.symbol}")
            return False
        
        # Calcular margen requerido aproximado
        market_data = self.connector.get_market_data(request.symbol)
        if not market_data:
            return False
        
        order_type_str = str(request.order_type.value) if hasattr(request.order_type, 'value') else str(request.order_type)
        price = market_data.ask if "BUY" in order_type_str.upper() else market_data.bid
        contract_size = symbol_info.trade_contract_size
        leverage = account_info.leverage
        
        required_margin = (request.volume * contract_size * price) / leverage
        
        # Dejar un buffer del 20%
        available_margin = account_info.margin_free * 0.8
        
        if required_margin > available_margin:
            logger.warning(f"⚠️ Margen insuficiente. Requerido: {required_margin:.2f}, "
                         f"Disponible: {available_margin:.2f}")
            return False
        
        return True
    
    def _check_risk_per_trade(self, account_info: AccountInfo, request: TradeRequest) -> bool:
        """
        Verifica si el riesgo por operación está dentro del límite
        
        Args:
            account_info: Información de la cuenta
            request: Solicitud de trading
            
        Returns:
            True si el riesgo está dentro del límite
        """
        if not request.stop_loss or not request.price:
            # Si no hay SL, no podemos calcular el riesgo
            return True
        
        # Obtener información del símbolo
        symbol_info = self.connector.get_symbol_info(request.symbol)
        if not symbol_info:
            return False
        
        # Calcular riesgo en puntos
        if "BUY" in str(request.order_type).upper():
            risk_points = abs(request.price - request.stop_loss)
        else:
            risk_points = abs(request.stop_loss - request.price)
        
        # Calcular riesgo en dinero
        point_value = symbol_info.point
        contract_size = symbol_info.trade_contract_size
        risk_money = risk_points * (contract_size * request.volume) * point_value
        
        # Calcular porcentaje de riesgo
        risk_percentage = risk_money / account_info.balance if account_info.balance > 0 else 0
        
        if risk_percentage > self.max_risk_per_trade:
            logger.warning(f"⚠️ Riesgo por operación excesivo: {risk_percentage*100:.2f}% "
                         f"(máx: {self.max_risk_per_trade*100}%)")
            return False
        
        logger.debug(f"Riesgo calculado: {risk_percentage*100:.2f}% ({risk_money:.2f} {account_info.currency})")
        return True
    
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        risk_percentage: Optional[float] = None
    ) -> Optional[float]:
        """
        Calcula el tamaño de posición óptimo basado en el riesgo
        
        Args:
            symbol: Símbolo del instrumento
            entry_price: Precio de entrada
            stop_loss: Precio de stop loss
            risk_percentage: Porcentaje de riesgo (usa default si no se especifica)
            
        Returns:
            Volumen calculado o None si hay error
        """
        account_info = self.connector.get_account_info()
        if not account_info:
            logger.error("No se pudo obtener información de cuenta")
            return None
        
        symbol_info = self.connector.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"No se pudo obtener información de {symbol}")
            return None
        
        # Usar riesgo por defecto si no se especifica
        risk_pct = risk_percentage or self.max_risk_per_trade
        
        # Calcular riesgo en dinero
        risk_money = account_info.balance * risk_pct
        
        # Calcular distancia al stop loss en puntos
        risk_points = abs(entry_price - stop_loss)
        
        if risk_points == 0:
            logger.error("Stop loss igual al precio de entrada")
            return None
        
        # Calcular tamaño de posición
        point_value = symbol_info.point
        contract_size = symbol_info.trade_contract_size
        
        volume = risk_money / (risk_points * contract_size * point_value)
        
        # Normalizar volumen
        volume = symbol_info.normalize_volume(volume)
        
        logger.info(f"Tamaño de posición calculado para {symbol}: {volume} lotes "
                   f"(Riesgo: {risk_pct*100}% = {risk_money:.2f} {account_info.currency})")
        
        return volume
    
    def get_risk_reward_ratio(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        is_buy: bool = True
    ) -> float:
        """
        Calcula el ratio riesgo/beneficio
        
        Args:
            entry_price: Precio de entrada
            stop_loss: Stop loss
            take_profit: Take profit
            is_buy: True si es compra, False si es venta
            
        Returns:
            Ratio riesgo/beneficio
        """
        if is_buy:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        
        if risk <= 0:
            return 0.0
        
        ratio = reward / risk
        return ratio
    
    def update_daily_stats(self, profit: float) -> None:
        """
        Actualiza estadísticas diarias después de cerrar una operación
        
        Args:
            profit: Ganancia/pérdida de la operación
        """
        self._daily_stats["trades_count"] += 1
        self._daily_stats["total_profit"] += profit
        
        if profit > 0:
            self._daily_stats["wins"] += 1
        else:
            self._daily_stats["losses"] += 1
        
        logger.info(f"Estadísticas actualizadas - Trades: {self._daily_stats['trades_count']}, "
                   f"Wins: {self._daily_stats['wins']}, "
                   f"Losses: {self._daily_stats['losses']}, "
                   f"P&L: {self._daily_stats['total_profit']:.2f}")
    
    def get_daily_stats(self) -> Dict:
        """
        Obtiene las estadísticas del día
        
        Returns:
            Diccionario con estadísticas diarias
        """
        stats = self._daily_stats.copy()
        
        if stats["trades_count"] > 0:
            stats["win_rate"] = (stats["wins"] / stats["trades_count"]) * 100
        else:
            stats["win_rate"] = 0.0
        
        return stats
    
    def is_trading_allowed(self) -> tuple[bool, str]:
        """
        Verifica si se permite operar según las reglas de riesgo
        
        Returns:
            Tupla (permitido, razón)
        """
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"
        
        # Verificar pérdida diaria
        if not self._check_daily_loss_limit(account_info):
            return False, "Límite de pérdida diaria alcanzado"
        
        # Verificar margin call
        if account_info.is_margin_call:
            return False, "Cuenta en margin call"
        
        # Verificar nivel de margen mínimo, pero solo si hay margen en uso (>0)
        # Cuando no hay operaciones abiertas, margin_level es 0.0
        if account_info.margin_level > 0 and account_info.margin_level < 200:
            return False, f"Nivel de margen bajo: {account_info.margin_level:.2f}%"
        
        return True, "Trading permitido"