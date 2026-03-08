from fastapi import APIRouter, Depends, HTTPException
from api.core.trading_service import TradingService

router = APIRouter()

def get_trading_service():
    from api.main import trading_service
    return trading_service

@router.get("/full/{symbol}")
async def get_full_analysis(symbol: str, service: TradingService = Depends(get_trading_service)):
    """
    Endpoint dedicado para el dashboard de análisis.
    Devuelve indicadores, tendencia y señales en una sola llamada.
    """
    analysis = service.get_market_analysis(symbol)
    if not analysis:
        raise HTTPException(status_code=404, detail="No se pudo analizar el mercado")
        
    # Enriquecer respuesta para el frontend
    return {
        "symbol": symbol,
        "price": analysis["current_price"],
        "trend_direction": analysis["trend"], # UPTREND, DOWNTREND, SIDEWAYS
        "signals": analysis["signals"], # BUY, SELL, NEUTRAL
        "indicators": analysis["indicators"],
        "support_resistance": analysis["levels"]
    }
