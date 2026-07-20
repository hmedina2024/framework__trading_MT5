"""
Script para iniciar el servidor de la API
"""
import uvicorn
import os
from dotenv import load_dotenv

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

    # Iniciar servidor Uvicorn
    # reload=True permite reiniciar el servidor automáticamente al cambiar código.
    # reload_dirs limita la vigilancia al código Python del backend: sin esto,
    # uvicorn vigila TODO el proyecto incluida frontend/, y cualquier edición de
    # JS/HTML reinicia el proceso completo, tumbando la conexión MT5 y los bots
    # activos (el frontend son archivos estáticos, no necesitan reload de Python).
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        reload_dirs=["api", "core", "strategies", "models", "platform_connector", "utils", "config"],
        log_level="info"
    )
