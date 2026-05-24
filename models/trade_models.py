"""
Modelos de datos para operaciones de trading usando Pydantic
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, Literal
from datetime import datetime
from enum import Enum

class OrderType(str, Enum):
    """Tipos de órdenes"""
    BUY = "BUY"
    SELL = "SELL"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"
    BUY_STOP_LIMIT = "BUY_STOP_LIMIT"
    SELL_STOP_LIMIT = "SELL_STOP_LIMIT"

class OrderStatus(str, Enum):
    """Estados de órdenes"""
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"

class TimeInForce(str, Enum):
    """Tiempo de vigencia de órdenes"""
    GTC = "GTC"  # Good Till Cancel
    DAY = "DAY"  # Day order
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill

class TradeRequest(BaseModel):
    """Modelo para solicitud de operación"""
    symbol: str = Field(..., description="Símbolo del instrumento")
    order_type: OrderType = Field(..., description="Tipo de orden")
    volume: float = Field(..., gt=0, description="Volumen de la operación")
    price: Optional[float] = Field(None, description="Precio de entrada")
    stop_loss: Optional[float] = Field(None, description="Stop Loss")
    take_profit: Optional[float] = Field(None, description="Take Profit")
    deviation: int = Field(20, description="Desviación máxima en puntos")
    magic_number: int = Field(234000, description="Número mágico")
    comment: str = Field("", description="Comentario de la orden")
    time_in_force: TimeInForce = Field(TimeInForce.GTC, description="Tiempo de vigencia")
    
    @validator('volume')
    def validate_volume(cls, v):
        if v <= 0:
            raise ValueError("El volumen debe ser mayor que 0")
        return round(v, 2)
    
    @validator('symbol')
    def validate_symbol(cls, v):
        return v.upper().strip()
    
    class Config:
        use_enum_values = True

class TradeResult(BaseModel):
    """Modelo para resultado de operación"""
    success: bool = Field(..., description="Si la operación fue exitosa")
    order_id: Optional[int] = Field(None, description="ID de la orden")
    ticket: Optional[int] = Field(None, description="Ticket de la operación")
    volume: Optional[float] = Field(None, description="Volumen ejecutado")
    price: Optional[float] = Field(None, description="Precio de ejecución")
    error_code: Optional[int] = Field(None, description="Código de error")
    error_message: Optional[str] = Field(None, description="Mensaje de error")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class Position(BaseModel):
    """Modelo para posición abierta"""
    ticket: int = Field(..., description="Ticket de la posición")
    symbol: str = Field(..., description="Símbolo")
    type: str = Field(..., description="Tipo (buy/sell)")
    volume: float = Field(..., description="Volumen")
    price_open: float = Field(..., description="Precio de apertura")
    price_current: float = Field(..., description="Precio actual")
    stop_loss: Optional[float] = Field(None, description="Stop Loss")
    take_profit: Optional[float] = Field(None, description="Take Profit")
    profit: float = Field(..., description="Ganancia/Pérdida")
    swap: float = Field(0.0, description="Swap")
    commission: float = Field(0.0, description="Comisión")
    magic_number: int = Field(..., description="Número mágico")
    comment: str = Field("", description="Comentario")
    time_open: datetime = Field(..., description="Tiempo de apertura")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class MarketData(BaseModel):
    """Modelo para datos de mercado"""
    symbol: str = Field(..., description="Símbolo")
    bid: float = Field(..., description="Precio de compra")
    ask: float = Field(..., description="Precio de venta")
    last: float = Field(..., description="Último precio")
    volume: float = Field(..., description="Volumen")
    time: datetime = Field(..., description="Timestamp")
    spread: float = Field(..., description="Spread en puntos")
    daily_change: float = Field(0.0, description="Cambio porcentual diario (Daily Change de MT5)")
    
    @property
    def mid_price(self) -> float:
        """Precio medio entre bid y ask"""
        return (self.bid + self.ask) / 2
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class AccountInfo(BaseModel):
    """Modelo para información de cuenta"""
    login: int = Field(..., description="Login de la cuenta")
    balance: float = Field(..., description="Balance")
    equity: float = Field(..., description="Equity")
    profit: float = Field(..., description="Ganancia flotante")
    margin: float = Field(..., description="Margen usado")
    margin_free: float = Field(..., description="Margen libre")
    margin_level: float = Field(..., description="Nivel de margen")
    leverage: int = Field(..., description="Apalancamiento")
    currency: str = Field(..., description="Moneda de la cuenta")
    server: str = Field(..., description="Servidor")
    company: str = Field(..., description="Compañía")
    
    @property
    def margin_percentage(self) -> float:
        """Porcentaje de margen usado"""
        if self.equity > 0:
            return (self.margin / self.equity) * 100
        return 0.0
    
    @property
    def is_margin_call(self) -> bool:
        """Verifica si hay margin call"""
        return self.margin_level < 100 if self.margin_level else False

class SymbolInfo(BaseModel):
    """Modelo para información de símbolo"""
    name: str = Field(..., description="Nombre del símbolo")
    description: str = Field(..., description="Descripción")
    point: float = Field(..., description="Tamaño del punto")
    tick_value: float = Field(..., description="Valor del tick para un lote")
    digits: int = Field(..., description="Dígitos decimales")
    spread: int = Field(..., description="Spread en puntos")
    trade_contract_size: float = Field(..., description="Tamaño del contrato")
    volume_min: float = Field(..., description="Volumen mínimo")
    volume_max: float = Field(..., description="Volumen máximo")
    volume_step: float = Field(..., description="Paso de volumen")
    trade_mode: int = Field(..., description="Modo de trading")
    
    def normalize_price(self, price: float) -> float:
        """Normaliza un precio según los dígitos del símbolo"""
        return round(price, self.digits)
    
    def normalize_volume(self, volume: float) -> float:
        """Normaliza un volumen según las reglas del símbolo"""
        volume = max(self.volume_min, min(volume, self.volume_max))
        steps = round((volume - self.volume_min) / self.volume_step)
        return self.volume_min + (steps * self.volume_step)