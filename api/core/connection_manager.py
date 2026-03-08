"""
Gestor de conexiones WebSocket
Mantiene y gestiona las conexiones activas con los clientes frontend
"""
from fastapi import WebSocket
from typing import List, Dict
import json
from utils import get_logger

logger = get_logger(__name__)

class ConnectionManager:
    """Gestor de WebSockets para actualizaciones en tiempo real"""
    
    def __init__(self):
        # Mantiene una lista de conexiones activas
        self.active_connections: List[WebSocket] = []
        # Mapa de suscripciones por símbolo (opcional para futuro)
        self.subscriptions: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket):
        """Acepta nueva conexión WebSocket"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Cliente WebSocket conectado. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Elimina conexión cerrada"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Cliente WebSocket desconectado. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Envía mensaje a todos los clientes conectados"""
        # Convertir a JSON string
        json_msg = json.dumps(message, default=str)
        
        for connection in self.active_connections:
            try:
                await connection.send_text(json_msg)
            except Exception as e:
                logger.error(f"Error enviando mensaje WS: {e}")
                # Posiblemente desconectar cliente fallido
