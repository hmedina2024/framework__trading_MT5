from fastapi import APIRouter, Depends, HTTPException, Body
from api.core.trading_service import TradingService
from models import Position, TradeRequest, TradeResult
from typing import List

router = APIRouter()

def get_trading_service():
    from api.main import trading_service
    return trading_service

@router.get("/", response_model=List[Position])
async def get_open_positions(service: TradingService = Depends(get_trading_service)):
    """Lista todas las posiciones abiertas actualmente"""
    return service.get_open_positions()

@router.post("/create", response_model=TradeResult)
async def create_order(request: TradeRequest, service: TradingService = Depends(get_trading_service)):
    """
    Envía una orden manual al mercado
    Validada primero por RiskManager
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")
        
    # Validar riesgo
    is_valid, msg = service.risk_manager.validate_trade(request)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Riesgo rechazada: {msg}")
        
    # Ejecutar orden
    result = service.order_manager.open_position(request)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error_message)
        
    return result

@router.post("/close/{ticket}")
async def close_position(ticket: int, service: TradingService = Depends(get_trading_service)):
    """Cierra una posición específica por su ticket"""
    result = service.order_manager.close_position(ticket)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error_message)
    return {"status": "closed", "ticket": ticket, "profit": result.profit}
