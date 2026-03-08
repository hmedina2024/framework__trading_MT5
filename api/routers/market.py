from fastapi import APIRouter, Depends, HTTPException, WebSocket
from api.core.trading_service import TradingService
from models import MarketData
from typing import List, Optional

router = APIRouter()

def get_trading_service():
    from api.main import trading_service
    return trading_service

@router.get("/ticker/{symbol}", response_model=MarketData)
async def get_ticker(symbol: str, service: TradingService = Depends(get_trading_service)):
    """Obtiene datos de mercado actuales para un símbolo"""
    data = service.get_market_data(symbol)
    if not data:
        raise HTTPException(status_code=404, detail=f"Símbolo {symbol} no encontrado o error MT5")
    return data

@router.get("/symbols")
async def get_available_symbols(service: TradingService = Depends(get_trading_service)):
    """Lista todos los símbolos disponibles en el broker"""
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")
    return service.connector.get_available_symbols()

@router.get("/analysis/{symbol}")
async def get_market_analysis(symbol: str, timeframe: int = 16385, service: TradingService = Depends(get_trading_service)):
    """
    Realiza un análisis técnico completo (H1 por defecto)
    Timeframes: 16385=H1, 16388=H4, 16408=D1
    """
    analysis = service.get_market_analysis(symbol, timeframe)
    if not analysis:
        raise HTTPException(status_code=400, detail="Error analizando mercado")
    return analysis
