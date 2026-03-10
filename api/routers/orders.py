from fastapi import APIRouter, Depends, HTTPException, Body
from api.core.trading_service import TradingService
from models import Position, TradeRequest, TradeResult
from typing import List

router = APIRouter()

# Códigos de error de MT5 que son errores del cliente (no del servidor)
# Estos se devuelven como HTTP 400 en lugar de 500
MT5_CLIENT_ERROR_CODES = {
    10027,  # AutoTrading disabled by client
    10018,  # Market closed
    10019,  # No money / insufficient funds
    10014,  # Invalid volume
    10016,  # Invalid stops
    10017,  # Trade disabled
    10004,  # Requote
    10008,  # Off quotes
    10013,  # Invalid request
    10015,  # Invalid price
}

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
    Envía una orden manual al mercado.
    Validada primero por RiskManager.
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
        error_code = result.error_code or 0
        http_status = 400 if error_code in MT5_CLIENT_ERROR_CODES else 500
        raise HTTPException(
            status_code=http_status,
            detail=result.error_message or "Error desconocido al ejecutar orden"
        )

    return result

@router.post("/close/{ticket}")
async def close_position(ticket: int, service: TradingService = Depends(get_trading_service)):
    """Cierra una posición específica por su ticket"""
    result = service.order_manager.close_position(ticket)
    if not result.success:
        error_code = result.error_code or 0
        http_status = 400 if error_code in MT5_CLIENT_ERROR_CODES else 500
        raise HTTPException(
            status_code=http_status,
            detail=result.error_message or "Error desconocido al cerrar posición"
        )
    return {"status": "closed", "ticket": ticket, "price": result.price, "volume": result.volume}