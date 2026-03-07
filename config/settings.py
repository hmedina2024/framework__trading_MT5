"""
Configuración centralizada del framework de trading MT5
"""
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from typing import Optional

# Cargar variables de entorno
load_dotenv(find_dotenv())

class Settings:
    """Configuración global del framework"""
    
    # Configuración de MT5
    MT5_PATH: str = os.getenv("MT5_PATH", "")
    MT5_LOGIN: Optional[int] = int(os.getenv("MT5_LOGIN")) if os.getenv("MT5_LOGIN") else None
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "")
    MT5_TIMEOUT: int = int(os.getenv("MT5_TIMEOUT", "60000"))
    MT5_PORTABLE: bool = os.getenv("MT5_PORTABLE", "False").lower() == "true"
    
    # Configuración de logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", "logs"))
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Configuración de trading
    DEFAULT_DEVIATION: int = int(os.getenv("DEFAULT_DEVIATION", "20"))
    DEFAULT_MAGIC_NUMBER: int = int(os.getenv("DEFAULT_MAGIC_NUMBER", "234000"))
    MAX_SLIPPAGE: int = int(os.getenv("MAX_SLIPPAGE", "10"))
    
    # Configuración de gestión de riesgo
    MAX_RISK_PER_TRADE: float = float(os.getenv("MAX_RISK_PER_TRADE", "0.02"))  # 2%
    MAX_DAILY_LOSS: float = float(os.getenv("MAX_DAILY_LOSS", "0.05"))  # 5%
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    
    @classmethod
    def validate(cls) -> bool:
        """Valida que la configuración esencial esté presente"""
        required_fields = [
            ("MT5_PATH", cls.MT5_PATH),
            ("MT5_LOGIN", cls.MT5_LOGIN),
            ("MT5_PASSWORD", cls.MT5_PASSWORD),
            ("MT5_SERVER", cls.MT5_SERVER),
        ]
        
        missing = [field for field, value in required_fields if not value]
        
        if missing:
            print(f"⚠️ Configuración incompleta. Faltan: {', '.join(missing)}")
            return False
        return True

settings = Settings()
