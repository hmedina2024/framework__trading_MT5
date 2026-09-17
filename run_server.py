"""
Script para iniciar el servidor de la API
"""
import uvicorn
import os
from dotenv import load_dotenv

# Cambiar al directorio del script para que todas las rutas relativas
# (frontend/, bots_config.json, stats_*.json, .env) funcionen correctamente
# sin importar desde qué carpeta se ejecute el script.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Cargar variables de entorno
load_dotenv()

if __name__ == "__main__":
    # Puerto configurable vía SERVER_PORT en .env — default 8000 (no afecta
    # EC2 ni ningún despliegue existente). Útil localmente si un puerto queda
    # ocupado por un proceso fantasma: basta con agregar SERVER_PORT=8001 al
    # .env LOCAL (nunca se commitea) sin tocar el comportamiento por defecto.
    PORT = int(os.getenv("SERVER_PORT", "8000"))

    # Imprimir información de acceso
    from utils.logger import get_logger
    logger = get_logger("SERVER")

    logger.info("="*60)
    logger.info(" INICIANDO SERVIDOR MT5 API ".center(60, "="))
    logger.info("="*60)
    logger.info(f"Backend API:      http://localhost:{PORT}")
    logger.info(f"Documentación:    http://localhost:{PORT}/docs")
    logger.info("Frontend:         Abre 'frontend/index.html' en tu navegador")
    logger.info("-" * 60)
    logger.info("Presiona CTRL+C para detener el servidor")

    # Abrir navegador automáticamente
    import webbrowser
    import threading
    import time

    def open_browser():
        time.sleep(1.5)  # Esperar a que el servidor inicie
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Thread(target=open_browser, daemon=True).start()

    # reload=True (uvicorn --reload) spawnea el worker real vía multiprocessing
    # y lo relanza cuando detecta cambios en reload_dirs. En este proyecto los
    # reinicios tras editar código siempre se hacen a mano (matando el árbol
    # completo del proceso), nunca dependiendo de este mecanismo — y dos
    # incidentes reales ya salieron de él: (1) al matar solo el proceso
    # "reloader" padre, el worker hijo quedó huérfano operando en vivo 6 días
    # sin que nadie lo supiera (ver core/instance_lock.py); (2) se observaron
    # decenas de procesos multiprocessing-fork acumulados tras ~2 días de
    # uptime sin ningún cambio real de archivos de por medio. Sin beneficio
    # real y con historial de causar bugs de duplicación — se desactiva por
    # default. RELOAD=true en .env lo reactiva para sesiones de desarrollo
    # activo si alguna vez hace falta.
    reload_enabled = os.getenv("RELOAD", "false").lower() == "true"
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=reload_enabled,
        reload_dirs=["api", "core", "strategies", "models", "platform_connector", "utils", "config"] if reload_enabled else None,
        log_level="info"
    )
