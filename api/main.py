"""
API Principal del Framework de Trading MT5
Servidor FastAPI con WebSockets para datos en tiempo real
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio

from api.routers import account, market, orders, strategies, analysis
from api.core.connection_manager import ConnectionManager
from api.core.trading_service import TradingService
from utils.logger import get_logger

logger = get_logger(__name__)

# Instancia global del servicio de trading
trading_service = TradingService()
connection_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación"""
    logger.info("🚀 Iniciando servidor de Trading MT5...")
    
    # Inicializar servicio de trading al arrancar
    success = await trading_service.initialize()
    if success:
        logger.info("✅ Servicio de trading inicializado correctamente")
    else:
        logger.warning("⚠️ Servicio de trading no pudo conectar a MT5 al inicio")
    
    # Guardar referencias en el estado de la app
    app.state.trading_service = trading_service
    app.state.connection_manager = connection_manager
    
    yield
    
    # Cleanup al cerrar
    logger.info("🛑 Cerrando servidor de Trading MT5...")
    await trading_service.shutdown()


# Crear aplicación FastAPI
app = FastAPI(
    title="MT5 Trading Platform API",
    description="""
    API REST y WebSocket para plataforma de trading automatizado con MetaTrader 5.
    
    ## Características
    - 📊 Datos de mercado en tiempo real via WebSocket
    - 🤖 Gestión de estrategias de trading automatizadas
    - 💰 Gestión de órdenes y posiciones
    - 📈 Análisis técnico con múltiples indicadores
    - 🛡️ Gestión de riesgo integrada
    """,
    version="1.0.0",
    lifespan=lifespan
)

# Configurar CORS para el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar routers
app.include_router(account.router, prefix="/api/v1/account", tags=["Account"])
app.include_router(market.router, prefix="/api/v1/market", tags=["Market"])
app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(strategies.router, prefix="/api/v1/strategies", tags=["Strategies"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])


# Montar archivos estáticos
app.mount("/css", StaticFiles(directory="frontend/css"), name="css")
app.mount("/js", StaticFiles(directory="frontend/js"), name="js")


@app.get("/", tags=["Frontend"])
async def root():
    """Servir la aplicación frontend"""
    return FileResponse('frontend/index.html')


@app.get("/api_status", tags=["Health"])
async def api_status():
    """Endpoint de estado de la API"""
    return {
        "status": "online",
        "service": "MT5 Trading Platform API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/api/v1/health", tags=["Health"])
async def health_check():
    """Verificar estado del servidor y conexión MT5"""
    is_connected = trading_service.is_connected()
    return {
        "status": "healthy" if is_connected else "degraded",
        "mt5_connected": is_connected,
        "active_strategies": trading_service.get_active_strategies_count(),
        "open_positions": trading_service.get_open_positions_count()
    }
