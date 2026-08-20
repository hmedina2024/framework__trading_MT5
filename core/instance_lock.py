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
lock sigue vivo. Se identifica al proceso por PID + create_time() (no por
línea de comandos: en modo --reload, uvicorn spawnea el worker real vía
multiprocessing, cuyo cmdline es un genérico "spawn_main(...)" que no
distingue nuestro proceso de cualquier otro). create_time() es el momento
exacto (con precisión de fracciones de segundo) en que el PID arrancó —
si Windows reutiliza el mismo número de PID para un proceso no
relacionado, su create_time será distinto y el lock se trata como
huérfano en vez de bloquear un arranque legítimo.
"""
from pathlib import Path
import os

import psutil

from utils.logger import get_logger

logger = get_logger(__name__)

LOCK_FILE = Path("server.lock")


class InstanceAlreadyRunningError(RuntimeError):
    pass


def _read_lock():
    """Devuelve (pid, create_time) del lock existente, o None si no es válido."""
    try:
        pid_str, ctime_str = LOCK_FILE.read_text().strip().split(",")
        return int(pid_str), float(ctime_str)
    except (ValueError, OSError):
        return None


def _is_same_process_still_alive(pid: int, create_time: float) -> bool:
    try:
        return abs(psutil.Process(pid).create_time() - create_time) < 1.0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def acquire() -> None:
    """
    Adquiere el lock de instancia única. Lanza InstanceAlreadyRunningError
    si ya hay otro worker vivo — quien llame debe abortar el arranque.
    """
    existing = _read_lock()
    if existing:
        old_pid, old_ctime = existing
        if _is_same_process_still_alive(old_pid, old_ctime):
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

    my_pid = os.getpid()
    my_ctime = psutil.Process(my_pid).create_time()
    LOCK_FILE.write_text(f"{my_pid},{my_ctime}")
    logger.info(f"Lock de instancia adquirido (PID {my_pid})")


def release() -> None:
    """Libera el lock, solo si sigue siendo nuestro (evita borrar el de otra instancia)."""
    try:
        existing = _read_lock()
        if existing and existing[0] == os.getpid():
            LOCK_FILE.unlink()
            logger.info("Lock de instancia liberado")
    except OSError as e:
        logger.warning(f"No se pudo liberar el lock de instancia: {e}")
