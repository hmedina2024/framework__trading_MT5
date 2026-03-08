"""
Script para iniciar el servidor de la API
"""
import uvicorn
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

if __name__ == "__main__":
    # Imprimir información de acceso
    from utils.logger import get_logger
    logger = get_logger("SERVER")
    
    logger.info("="*60)
    logger.info(" INICIANDO SERVIDOR MT5 API ".center(60, "="))
    logger.info("="*60)
    logger.info("Backend API:      http://localhost:8000")
    logger.info("Documentación:    http://localhost:8000/docs")
    logger.info("Frontend:         Abre 'frontend/index.html' en tu navegador")
    logger.info("-" * 60)
    logger.info("Presiona CTRL+C para detener el servidor")
    
    # Abrir navegador automáticamente
    import webbrowser
    import threading
    import time

    def open_browser():
        time.sleep(1.5)  # Esperar a que el servidor inicie
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=open_browser, daemon=True).start()

    # Iniciar servidor Uvicorn
    # reload=True permite reiniciar el servidor automáticamente al cambiar código
    uvicorn.run(
        "api.main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )
