from fastapi import APIRouter, Depends, HTTPException
from api.core.trading_service import TradingService
from models import AccountInfo

router = APIRouter()

def get_trading_service():
    # Obtener la instancia global
    from api.main import trading_service
    return trading_service

@router.get("/info", response_model=AccountInfo)
async def get_account_info(service: TradingService = Depends(get_trading_service)):
    """Obtiene la información detallada de la cuenta"""
    info = service.get_account_info()
    if not info:
        raise HTTPException(status_code=503, detail="MT5 no conectado o error obteniendo cuenta")
    return info

@router.get("/status")
async def get_connection_status(service: TradingService = Depends(get_trading_service)):
    """Verifica estado de conexión específico"""
    return {
        "connected": service.is_connected(),
        "server": service.connector.get_account_info().server if service.is_connected() else None
    }
