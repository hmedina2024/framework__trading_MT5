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


@router.get("/ml-status")
async def get_ml_status():
    """
    Estado del filtro de señales con ML (SignalFilter).
    Permite monitorear desde /docs o el frontend si el modelo LightGBM ya se
    activó, cuántas muestras etiquetadas tiene, cuántas faltan para entrenar,
    el win rate de las muestras y la importancia de cada feature.
    """
    try:
        from core.signal_filter import signal_filter
        return signal_filter.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado ML: {e}")
