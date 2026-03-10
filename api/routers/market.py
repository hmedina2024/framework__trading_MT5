from fastapi import APIRouter, Depends, HTTPException, WebSocket
from api.core.trading_service import TradingService
from models import MarketData
from typing import List, Optional
import MetaTrader5 as mt5

router = APIRouter()

# Mapa de minutos a constante MT5
TIMEFRAME_MINUTES_MAP = {
    1:    mt5.TIMEFRAME_M1,
    5:    mt5.TIMEFRAME_M5,
    15:   mt5.TIMEFRAME_M15,
    30:   mt5.TIMEFRAME_M30,
    60:   mt5.TIMEFRAME_H1,
    240:  mt5.TIMEFRAME_H4,
    1440: mt5.TIMEFRAME_D1,
}

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

@router.get("/candles/{symbol}")
async def get_candles(
    symbol: str,
    timeframe: int = 60,
    count: int = 200,
    service: TradingService = Depends(get_trading_service)
):
    """
    Devuelve velas OHLC históricas para el gráfico.
    timeframe: minutos (1, 5, 15, 30, 60, 240, 1440)
    count: número de velas (máx 500)
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")

    if service.market_analyzer is None:
        raise HTTPException(status_code=503, detail="MarketAnalyzer no inicializado")

    mt5_tf = TIMEFRAME_MINUTES_MAP.get(timeframe, mt5.TIMEFRAME_H1)
    count = min(count, 500)

    try:
        df = service.market_analyzer.get_candles(symbol, mt5_tf, count)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo velas: {str(e)}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"No se obtuvieron velas para {symbol} en timeframe {timeframe}m")

    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time":   row["time"].isoformat(),
            "open":   float(row["open"]),
            "high":   float(row["high"]),
            "low":    float(row["low"]),
            "close":  float(row["close"]),
            "volume": float(row.get("tick_volume", 0)),
        })

    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}

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
