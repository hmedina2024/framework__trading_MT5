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

@router.post("/backtest")
async def run_backtest(
    symbol: str,
    strategy_type: str = "MACD",
    days: int = 90,
    initial_balance: float = 1000.0,
    risk_pct: float = 0.01,
    service: TradingService = Depends(get_trading_service)
):
    """
    Ejecuta un backtest de la estrategia sobre datos históricos de MT5.
    No abre posiciones reales — simulación completa walk-forward.

    Params:
      symbol:          par a testear (EURUSD, XAUUSD, etc.)
      strategy_type:   tipo de estrategia (MACD, BOLLINGER, EMA_CROSS, etc.)
      days:            días de historial a usar (30-365)
      initial_balance: balance inicial de simulación (default 1000)
      risk_pct:        riesgo por trade como decimal (0.01 = 1%)
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")

    if days < 10 or days > 365:
        raise HTTPException(status_code=400, detail="days debe estar entre 10 y 365")

    result = service.run_backtest(
        symbol=symbol.upper(),
        strategy_type=strategy_type.upper(),
        days=days,
        initial_balance=initial_balance,
        risk_pct=risk_pct
    )

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])

    return result