from fastapi import APIRouter, Depends, HTTPException
from api.core.trading_service import TradingService
from core.circuit_breaker import circuit_breaker
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
    strategy_type: str,
    days: int = 90,
    initial_balance: float = 1000.0,
    risk_pct: float = 0.01,
    service: TradingService = Depends(get_trading_service)
):
    """
    Ejecuta un backtest walk-forward simulado sobre datos históricos de MT5.
    No abre posiciones reales. El endpoint que el frontend (página Backtest)
    llama desde hace tiempo pero nunca estuvo conectado — trading_service ya
    tenía el motor implementado (run_backtest), solo faltaba esta ruta.
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, service.run_backtest, symbol, strategy_type, days, initial_balance, risk_pct
    )
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@router.get("/regime")
async def get_regime_status(service: TradingService = Depends(get_trading_service)):
    """
    Retorna el régimen de mercado actual para todos los símbolos.
    Incluye: régimen (TRENDING_UP/DOWN, RANGING, VOLATILE),
    ADX, pendiente EMA200, ratio ATR y estrategias recomendadas.
    """
    return service.get_regime_status()

@router.post("/regime/refresh")
async def force_regime_refresh(service: TradingService = Depends(get_trading_service)):
    """
    Fuerza una re-evaluación inmediata de todos los regímenes.
    Inicia/detiene bots según el régimen detectado.
    """
    if not service.is_connected():
        raise HTTPException(status_code=503, detail="MT5 no conectado")
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, service.force_regime_update)
    return result


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

@router.get("/circuit-breaker")
async def get_circuit_breaker_all():
    """
    Retorna el estado del circuit breaker de todos los bots conocidos.
    Incluye: estado (CLOSED/OPEN/HALF_OPEN), pérdida acumulada, umbral y tiempo de recuperación.
    """
    return circuit_breaker.get_all_status()


@router.get("/circuit-breaker/{strategy_id}")
async def get_circuit_breaker_by_id(strategy_id: str):
    """
    Retorna el estado del circuit breaker de un bot específico.
    """
    status = circuit_breaker.get_status(strategy_id)
    return status


@router.post("/circuit-breaker/{strategy_id}/reset")
async def reset_circuit_breaker(strategy_id: str):
    """
    Resetea manualmente el circuit breaker de un bot bloqueado.
    Vuelve al estado CLOSED y reinicia los contadores de pérdida.
    """
    ok = circuit_breaker.manual_reset(strategy_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró estado de circuit breaker para '{strategy_id}'"
        )
    return {"status": "reset", "strategy_id": strategy_id, "new_state": "CLOSED"}
