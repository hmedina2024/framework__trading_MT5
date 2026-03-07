"""
Sistema de logging centralizado para el framework
"""
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from config.settings import settings

class LoggerSetup:
    """Configuración centralizada de logging"""
    
    _loggers = {}
    
    @classmethod
    def get_logger(cls, name: str, log_file: Optional[str] = None) -> logging.Logger:
        """
        Obtiene o crea un logger configurado
        
        Args:
            name: Nombre del logger
            log_file: Nombre del archivo de log (opcional)
            
        Returns:
            Logger configurado
        """
        if name in cls._loggers:
            return cls._loggers[name]
        
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL))
        
        # Evitar duplicación de handlers
        if logger.handlers:
            return logger
        
        # Formato del log
        formatter = logging.Formatter(settings.LOG_FORMAT)
        
        # Handler para consola
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # Handler para archivo
        if log_file or settings.LOG_DIR:
            settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
            
            if not log_file:
                log_file = f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
            
            file_handler = logging.FileHandler(
                settings.LOG_DIR / log_file,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        cls._loggers[name] = logger
        return logger

def get_logger(name: str) -> logging.Logger:
    """Función helper para obtener un logger"""
    return LoggerSetup.get_logger(name)
