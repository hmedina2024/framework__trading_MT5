"""
Funciones auxiliares y utilidades para el framework
"""
import MetaTrader5 as mt5
from typing import Dict, Optional
from datetime import datetime, timedelta
import json

def timeframe_to_string(timeframe: int) -> str:
    """
    Convierte constante de timeframe a string legible
    
    Args:
        timeframe: Constante de MT5 timeframe
        
    Returns:
        String representando el timeframe
    """
    timeframes = {
        mt5.TIMEFRAME_M1: "M1",
        mt5.TIMEFRAME_M5: "M5",
        mt5.TIMEFRAME_M15: "M15",
        mt5.TIMEFRAME_M30: "M30",
        mt5.TIMEFRAME_H1: "H1",
        mt5.TIMEFRAME_H4: "H4",
        mt5.TIMEFRAME_D1: "D1",
        mt5.TIMEFRAME_W1: "W1",
        mt5.TIMEFRAME_MN1: "MN1"
    }
    return timeframes.get(timeframe, f"TF_{timeframe}")

def string_to_timeframe(timeframe_str: str) -> Optional[int]:
    """
    Convierte string a constante de timeframe
    
    Args:
        timeframe_str: String del timeframe (ej: "H1", "M15")
        
    Returns:
        Constante de MT5 o None si no es válido
    """
    timeframes = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1
    }
    return timeframes.get(timeframe_str.upper())

def format_currency(amount: float, currency: str = "USD") -> str:
    """
    Formatea cantidad de dinero
    
    Args:
        amount: Cantidad
        currency: Moneda
        
    Returns:
        String formateado
    """
    return f"{amount:,.2f} {currency}"

def calculate_pip_value(symbol_info, volume: float = 1.0) -> float:
    """
    Calcula el valor de un pip
    
    Args:
        symbol_info: Información del símbolo
        volume: Volumen en lotes
        
    Returns:
        Valor del pip
    """
    return symbol_info.point * symbol_info.trade_contract_size * volume

def points_to_price(points: float, symbol_info) -> float:
    """
    Convierte puntos a precio
    
    Args:
        points: Número de puntos
        symbol_info: Información del símbolo
        
    Returns:
        Precio equivalente
    """
    return points * symbol_info.point

def price_to_points(price_diff: float, symbol_info) -> float:
    """
    Convierte diferencia de precio a puntos
    
    Args:
        price_diff: Diferencia de precio
        symbol_info: Información del símbolo
        
    Returns:
        Número de puntos
    """
    return price_diff / symbol_info.point

def is_market_open(symbol_info) -> bool:
    """
    Verifica si el mercado está abierto para un símbolo
    
    Args:
        symbol_info: Información del símbolo
        
    Returns:
        True si el mercado está abierto
    """
    # Verificar si el símbolo permite trading
    return symbol_info.trade_mode in [
        mt5.SYMBOL_TRADE_MODE_FULL,
        mt5.SYMBOL_TRADE_MODE_LONGONLY,
        mt5.SYMBOL_TRADE_MODE_SHORTONLY
    ]

def get_trading_hours(symbol: str) -> Dict:
    """
    Obtiene horarios de trading de un símbolo
    
    Args:
        symbol: Nombre del símbolo
        
    Returns:
        Diccionario con información de horarios
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return {}
    
    return {
        'symbol': symbol,
        'trade_mode': info.trade_mode,
        'is_tradeable': is_market_open(info),
        'session_deals': info.session_deals,
        'session_buy_orders': info.session_buy_orders,
        'session_sell_orders': info.session_sell_orders
    }

def save_to_json(data: Dict, filename: str) -> bool:
    """
    Guarda datos en archivo JSON
    
    Args:
        data: Datos a guardar
        filename: Nombre del archivo
        
    Returns:
        True si fue exitoso
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, default=str)
        return True
    except Exception as e:
        print(f"Error al guardar JSON: {e}")
        return False

def load_from_json(filename: str) -> Optional[Dict]:
    """
    Carga datos desde archivo JSON
    
    Args:
        filename: Nombre del archivo
        
    Returns:
        Diccionario con datos o None si hay error
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error al cargar JSON: {e}")
        return None

def format_position_info(position) -> str:
    """
    Formatea información de posición para mostrar
    
    Args:
        position: Objeto Position
        
    Returns:
        String formateado
    """
    return (f"Ticket: {position.ticket} | "
            f"{position.symbol} | "
            f"{position.type} | "
            f"Vol: {position.volume} | "
            f"Entry: {position.price_open} | "
            f"Current: {position.price_current} | "
            f"P&L: {position.profit:.2f}")

def calculate_lot_size_from_risk(
    balance: float,
    risk_percentage: float,
    stop_loss_pips: float,
    pip_value: float
) -> float:
    """
    Calcula tamaño de lote basado en riesgo
    
    Args:
        balance: Balance de la cuenta
        risk_percentage: Porcentaje de riesgo (0.02 = 2%)
        stop_loss_pips: Distancia del SL en pips
        pip_value: Valor de un pip
        
    Returns:
        Tamaño de lote calculado
    """
    risk_amount = balance * risk_percentage
    lot_size = risk_amount / (stop_loss_pips * pip_value)
    return round(lot_size, 2)

def get_market_state(rsi: float, macd_histogram: float) -> str:
    """
    Determina estado del mercado basado en indicadores
    
    Args:
        rsi: Valor del RSI
        macd_histogram: Valor del histograma MACD
        
    Returns:
        Estado del mercado
    """
    if rsi > 70 and macd_histogram > 0:
        return "STRONG_BULLISH"
    elif rsi > 50 and macd_histogram > 0:
        return "BULLISH"
    elif rsi < 30 and macd_histogram < 0:
        return "STRONG_BEARISH"
    elif rsi < 50 and macd_histogram < 0:
        return "BEARISH"
    else:
        return "NEUTRAL"

def validate_symbol_format(symbol: str) -> bool:
    """
    Valida formato de símbolo
    
    Args:
        symbol: Símbolo a validar
        
    Returns:
        True si es válido
    """
    # Validación básica
    if not symbol or len(symbol) < 3:
        return False
    
    # Debe contener solo letras, números y algunos caracteres especiales
    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return all(c in allowed_chars for c in symbol.upper())

def get_session_name() -> str:
    """
    Obtiene el nombre de la sesión de trading actual
    
    Returns:
        Nombre de la sesión
    """
    now = datetime.now()
    hour = now.hour
    
    # Horarios aproximados de sesiones (UTC)
    if 0 <= hour < 8:
        return "ASIAN"
    elif 8 <= hour < 16:
        return "EUROPEAN"
    elif 16 <= hour < 24:
        return "AMERICAN"
    else:
        return "UNKNOWN"

def is_weekend() -> bool:
    """
    Verifica si es fin de semana
    
    Returns:
        True si es sábado o domingo
    """
    return datetime.now().weekday() >= 5

def next_trading_day() -> datetime:
    """
    Calcula el próximo día de trading
    
    Returns:
        Fecha del próximo día de trading
    """
    now = datetime.now()
    days_ahead = 0
    
    # Si es viernes después de cierre, siguiente es lunes
    if now.weekday() == 4 and now.hour >= 22:
        days_ahead = 3
    # Si es sábado
    elif now.weekday() == 5:
        days_ahead = 2
    # Si es domingo
    elif now.weekday() == 6:
        days_ahead = 1
    
    return now + timedelta(days=days_ahead)
