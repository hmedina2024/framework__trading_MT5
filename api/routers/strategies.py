from fastapi import APIRouter, Depends, HTTPException
from api.core.trading_service import TradingService
from typing import List, Dict

router = APIRouter()

def get_trading_service():
    from api.main import trading_service
    return trading_service

@router.get("/catalog")
async def get_strategy_catalog(service: TradingService = Depends(get_trading_service)):
    """Retorna el catálogo de estrategias disponibles para seleccionar en el frontend"""
    return service.get_strategy_catalog()

@router.get("/")
async def list_active_strategies(service: TradingService = Depends(get_trading_service)):
    """Lista todas las estrategias automáticas corriendo"""
    return service.get_strategies_status()

@router.post("/start/{symbol}")
async def start_strategy(
    symbol: str,
    strategy_type: str = "MA_CROSS",
    service: TradingService = Depends(get_trading_service)
):
    """Inicia un bot automático para un símbolo con la estrategia seleccionada"""
    success = service.start_strategy(symbol, strategy_type)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="No se pudo iniciar la estrategia (quizás ya existe o error MT5)"
        )
    return {"status": "started", "symbol": symbol, "strategy": strategy_type}

@router.post("/stop/{strategy_id}")
async def stop_strategy(strategy_id: str, service: TradingService = Depends(get_trading_service)):
    """Detiene una estrategia específica"""
    success = service.stop_strategy(strategy_id)
    if not success:
        raise HTTPException(status_code=404, detail="Estrategia no encontrada")
    return {"status": "stopped", "id": strategy_id}
