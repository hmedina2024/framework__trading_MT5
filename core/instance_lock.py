"""
Lock de instancia única para el proceso worker que corre TradingService.

Motivación: el 2026-08-20 se descubrió que dos procesos completos del
servidor estuvieron corriendo en paralelo durante 6 días, cada uno con su
propio set de bots conectado a la MISMA cuenta MT5 — resultó en tickets
duplicados en vivo (dos posiciones reales por una sola señal). La causa:
al detener el servidor matando el proceso "reloader" de uvicorn, Windows
no mata en cascada a sus hijos, y el worker real (el que corre los hilos
de las estrategias y mantiene la conexión a MT5) quedó huérfano — sin
puerto, invisible a /health, pero completamente vivo y operando.

Este lock hace que un segundo worker se niegue a arrancar si el PID del
lock sigue vivo Y su línea de comandos coincide con la nuestra (evita
falsos positivos si Windows reutiliza el mismo PID para un proceso
no relacionado).
"""
from pathlib import Path
import os
import sys

import psutil

from utils.logger import get_logger

logger = get_logger(__name__)

LOCK_FILE = Path("server.lock")


class InstanceAlreadyRunningError(RuntimeError):
    pass


def _cmdline_matches(pid: int) -> bool:
    """True si el proceso con ese PID sigue vivo y parece ser este mismo servidor."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        return "run_server.py" in cmdline or "uvicorn" in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def acquire() -> None:
    """
    Adquiere el lock de instancia única. Lanza InstanceAlreadyRunningError
    si ya hay otro worker vivo — quien llame debe abortar el arranque.
    """
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = None

        if old_pid and _cmdline_matches(old_pid):
            raise InstanceAlreadyRunningError(
                f"Ya hay una instancia del servidor corriendo (PID {old_pid}). "
                f"Si estás seguro de que ya no está activa, detenla manualmente "
                f"(Stop-Process -Id {old_pid} -Force) y borra {LOCK_FILE} antes "
                f"de reintentar."
            )
        else:
            logger.warning(
                f"Lock huérfano encontrado (PID {old_pid} ya no está vivo) — "
                f"se reemplaza."
            )

    LOCK_FILE.write_text(str(os.getpid()))
    logger.info(f"Lock de instancia adquirido (PID {os.getpid()})")


def release() -> None:
    """Libera el lock, solo si sigue siendo nuestro (evita borrar el de otra instancia)."""
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
            logger.info("Lock de instancia liberado")
    except OSError as e:
        logger.warning(f"No se pudo liberar el lock de instancia: {e}")
