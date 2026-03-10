"""
Gestor de riesgo para operaciones de trading
"""
from typing import Optional, Dict

from utils.logger import get_logger
from models.trade_models import TradeRequest, AccountInfo
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
        max_daily_loss: Optional[float] = None,
        max_open_positions: Optional[int] = None
    ):
        self.connector = connector
        self.max_risk_per_trade = settings.MAX_RISK_PER_TRADE 
        self.max_daily_loss = max_daily_loss or settings.MAX_DAILY_LOSS
        self.max_open_positions = max_open_positions or settings.MAX_OPEN_POSITIONS
        
        logger.info(f"RiskManager inicializado - Pérdida diaria máx: {self.max_daily_loss*100}%, "
                   f"Posiciones máx: {self.max_open_positions}")

    def validate_trade(self, request: TradeRequest) -> tuple[bool, str]:
        """Valida si una operación cumple con las reglas de riesgo globales."""
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"

        allowed, reason = self.is_trading_allowed()
        if not allowed:
            return False, reason

        if not self._check_margin_available(account_info, request):
            return False, "Margen insuficiente para la operación"
            
        logger.info(f"✅ Validación de riesgo aprobada para {request.symbol}")
        return True, "Operación aprobada"

    def _check_max_positions(self) -> bool:
        """Verifica si se ha alcanzado el número máximo de posiciones."""
        try:
            positions = self.connector.get_positions()
            current_positions = len(positions) if positions else 0
            if current_positions >= self.max_open_positions:
                logger.warning(f"⚠️ Máximo de posiciones alcanzado: {current_positions}/{self.max_open_positions}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error al verificar posiciones abiertas: {e}")
            return False

    def _check_margin_available(self, account_info: AccountInfo, request: TradeRequest) -> bool:
        """Verifica si hay margen suficiente para la operación."""
        symbol_info = self.connector.get_symbol_info(request.symbol)
        if not symbol_info:
            return False
        
        price = request.price
        if not price: # Si es una orden de mercado, obtener precio actual
            market_data = self.connector.get_market_data(request.symbol)
            if not market_data: return False
            order_type_str = str(request.order_type.value) if hasattr(request.order_type, 'value') else str(request.order_type)
            price = market_data.ask if "BUY" in order_type_str.upper() else market_data.bid

        if account_info.leverage <= 0:
             return False

        required_margin = (request.volume * symbol_info.trade_contract_size * price) / account_info.leverage
        
        if required_margin > account_info.margin_free:
            logger.warning(f"⚠️ Margen insuficiente. Requerido: {required_margin:.2f}, "
                         f"Disponible: {account_info.margin_free:.2f}")
            return False
        
        return True

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        risk_percentage: Optional[float] = None
    ) -> Optional[float]:
        """Calcula el tamaño de posición óptimo basado en el riesgo."""
        account_info = self.connector.get_account_info()
        if not account_info:
            logger.error("No se pudo obtener info de cuenta para calcular tamaño de posición")
            return None
        
        symbol_info = self.connector.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"No se pudo obtener info de {symbol} para calcular tamaño de posición")
            return None

        risk_pct = risk_percentage or self.max_risk_per_trade
        risk_money = account_info.equity * risk_pct
        risk_points = abs(entry_price - stop_loss_price)
        
        if risk_points <= 0 or symbol_info.point <= 0 or symbol_info.tick_value <= 0:
            logger.error(f"División por cero o valores inválidos en cálculo de volumen. Risk points: {risk_points}, point: {symbol_info.point}, tick_value: {symbol_info.tick_value}")
            return None
            
        risk_per_lot = (risk_points / symbol_info.point) * symbol_info.tick_value
        
        if risk_per_lot <= 0:
            logger.error(f"El riesgo por lote es cero o negativo ({risk_per_lot}).")
            return None
            
        volume = risk_money / risk_per_lot
        volume = symbol_info.normalize_volume(volume)
        
        logger.info(f"Tamaño de posición calculado para {symbol}: {volume:.2f} lotes (Riesgo: {risk_pct*100}%)")
        return volume

    def get_risk_reward_ratio(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        is_buy: bool = True
    ) -> float:
        """Calcula el ratio riesgo/beneficio."""
        if is_buy:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        
        if risk <= 0: return 0.0
        return reward / risk

    def is_trading_allowed(self) -> tuple[bool, str]:
        """Verifica si se permite operar según las reglas de riesgo globales."""
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"
        
        if account_info.is_margin_call:
            return False, "Cuenta en margin call"
            
        if account_info.margin > 0 and account_info.margin_level < 200:
             return False, f"Nivel de margen bajo: {account_info.margin_level:.2f}%"
        
        if not self._check_max_positions():
             return False, f"Número máximo de posiciones alcanzado ({self.max_open_positions})"

        return True, "Trading permitido"
