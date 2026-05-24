"""
Circuit Breaker por estrategia + símbolo

Detiene automáticamente un bot cuando su pérdida acumulada supera MAX_LOSS_USD.
El estado persiste en disco (circuit_breaker_state.json) para sobrevivir reinicios.

Lógica:
  - Cada bot tiene un "contador de pérdida acumulada" por sesión de recuperación.
  - Si las pérdidas superan MAX_LOSS_USD → estado OPEN (bot bloqueado).
  - En estado OPEN, el bot no puede abrir nuevas posiciones.
  - Recuperación automática: después de RECOVERY_HOURS horas, pasa a HALF_OPEN.
  - En HALF_OPEN: el bot puede operar de nuevo pero con monitoreo estricto.
  - Si gana un trade en HALF_OPEN → estado CLOSED (recuperado, contadores reinician).
  - Si pierde otro trade en HALF_OPEN → vuelve a OPEN (otra espera de RECOVERY_HOURS).

Estados:
  CLOSED    — operando normalmente
  OPEN      — bloqueado por pérdidas excesivas
  HALF_OPEN — período de prueba tras recuperación

Persistencia:
  Archivo: circuit_breaker_state.json (en la raíz del proyecto)
  Formato: { "BOLLINGER_GBPUSD": { "state": "OPEN", "loss": -143.5, ... }, ... }
"""
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuración — ajustar según necesidades
# ---------------------------------------------------------------------------

# Pérdida máxima acumulada por bot antes de disparar el circuit breaker
MAX_LOSS_USD = -50.0  # negativo = pérdida

# Horas de espera antes de pasar de OPEN a HALF_OPEN (intento de recuperación)
RECOVERY_HOURS = 24.0

# Archivo de persistencia
CB_STATE_FILE = Path("circuit_breaker_state.json")

# ---------------------------------------------------------------------------


class CircuitBreakerState:
    CLOSED    = "CLOSED"     # operando normalmente
    OPEN      = "OPEN"       # bloqueado
    HALF_OPEN = "HALF_OPEN"  # período de prueba


class CircuitBreaker:
    """
    Gestor de circuit breakers para todos los bots activos.
    Singleton — una sola instancia compartida por todo el sistema.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._data: Dict[str, Dict] = {}
                cls._instance._dirty = False
                cls._instance._initialized = False
                # Lock separado para proteger operaciones de datos entre hilos.
                # _lock protege la creación del singleton; _data_lock protege
                # las lecturas/escrituras concurrentes desde múltiples strategy threads.
                cls._instance._data_lock = threading.RLock()
            return cls._instance

    def initialize(self) -> None:
        """Carga el estado persistido. Llamar una vez al arrancar el servidor."""
        if self._initialized:
            return
        with self._data_lock:
            self._load()
        self._initialized = True
        logger.info(
            f"CircuitBreaker inicializado — "
            f"{len(self._data)} bots con estado guardado | "
            f"umbral: ${abs(MAX_LOSS_USD):.0f} | recuperación: {RECOVERY_HOURS}h"
        )
        # Loguear bots bloqueados al arrancar
        blocked = [k for k, v in self._data.items() if v.get("state") != CircuitBreakerState.CLOSED]
        if blocked:
            logger.warning(f"CircuitBreaker: bots bloqueados al arrancar: {blocked}")

    # -----------------------------------------------------------------------
    # API pública — llamar desde StrategyBase
    # -----------------------------------------------------------------------

    def is_allowed(self, strategy_id: str) -> Tuple[bool, str]:
        """
        Retorna (True, "") si el bot puede operar.
        Retorna (False, reason) si está bloqueado.

        También transiciona OPEN→HALF_OPEN automáticamente si ya pasó
        el período de recuperación, sin necesidad de un scheduler externo.
        Usa _data_lock (RLock) para ser thread-safe entre múltiples strategy threads.
        """
        with self._data_lock:
            entry = self._get_or_create(strategy_id)
            state = entry["state"]

            if state == CircuitBreakerState.CLOSED:
                return True, ""

            if state == CircuitBreakerState.OPEN:
                triggered_at = entry.get("triggered_at")
                if triggered_at:
                    try:
                        dt = datetime.fromisoformat(triggered_at)
                        hours_elapsed = (datetime.now() - dt).total_seconds() / 3600
                        if hours_elapsed >= RECOVERY_HOURS:
                            self._transition(strategy_id, CircuitBreakerState.HALF_OPEN)
                            logger.info(
                                f"CircuitBreaker {strategy_id}: OPEN → HALF_OPEN "
                                f"({hours_elapsed:.1f}h transcurridas) — modo prueba activo"
                            )
                            return True, ""
                        remaining_h = RECOVERY_HOURS - hours_elapsed
                        return False, (
                            f"Circuit breaker ABIERTO — pérdida acumulada "
                            f"${entry.get('accumulated_loss', 0):.2f} "
                            f"(umbral ${abs(MAX_LOSS_USD):.0f}) | "
                            f"recuperación en {remaining_h:.1f}h"
                        )
                    except (ValueError, TypeError):
                        pass
                return False, f"Circuit breaker ABIERTO para {strategy_id}"

            if state == CircuitBreakerState.HALF_OPEN:
                return True, ""

            return True, ""

    def record_trade_result(self, strategy_id: str, profit: float) -> None:
        """
        Registra el resultado de un trade cerrado.
        Actualiza pérdida acumulada y dispara/recupera el circuit breaker.
        """
        with self._data_lock:
            self._record_trade_result_locked(strategy_id, profit)

    def _record_trade_result_locked(self, strategy_id: str, profit: float) -> None:
        """Implementación interna — debe llamarse con _data_lock adquirido."""
        entry = self._get_or_create(strategy_id)
        state = entry["state"]

        if state == CircuitBreakerState.HALF_OPEN:
            if profit >= 0:
                # Trade ganador en HALF_OPEN → recuperado
                logger.info(
                    f"CircuitBreaker {strategy_id}: HALF_OPEN → CLOSED "
                    f"(trade ganador ${profit:.2f}) — contadores reiniciados"
                )
                self._reset(strategy_id)
                return
            else:
                # Otro trade perdedor en HALF_OPEN → volver a OPEN
                logger.warning(
                    f"CircuitBreaker {strategy_id}: HALF_OPEN → OPEN "
                    f"(trade perdedor ${profit:.2f}) — nueva espera de {RECOVERY_HOURS}h"
                )
                entry["accumulated_loss"] = entry.get("accumulated_loss", 0) + profit
                self._trip(strategy_id, entry["accumulated_loss"])
                return

        if state == CircuitBreakerState.OPEN:
            return  # ya bloqueado, no acumular más

        # Estado CLOSED — acumular solo si es pérdida
        if profit < 0:
            entry["accumulated_loss"] = entry.get("accumulated_loss", 0.0) + profit
            entry["trades_since_reset"] = entry.get("trades_since_reset", 0) + 1

            logger.info(
                f"CircuitBreaker {strategy_id}: pérdida registrada ${profit:.2f} | "
                f"acumulado ${entry['accumulated_loss']:.2f} / ${abs(MAX_LOSS_USD):.0f}"
            )

            if entry["accumulated_loss"] <= MAX_LOSS_USD:
                self._trip(strategy_id, entry["accumulated_loss"])
        else:
            # Trade ganador en CLOSED: reducir la pérdida acumulada (parcialmente)
            # Esto evita que un bot con 1 pérdida grande y 10 ganancias quede
            # eternamente cerca del umbral. Se reduce hasta 0 como máximo.
            entry["accumulated_loss"] = min(
                0.0,
                entry.get("accumulated_loss", 0.0) + profit
            )
            entry["trades_since_reset"] = entry.get("trades_since_reset", 0) + 1

        self._save()

    def get_status(self, strategy_id: str) -> Dict:
        """Devuelve el estado completo del circuit breaker para un bot."""
        entry = self._get_or_create(strategy_id)
        return {
            "strategy_id":       strategy_id,
            "state":             entry["state"],
            "accumulated_loss":  round(entry.get("accumulated_loss", 0.0), 2),
            "max_loss_usd":      MAX_LOSS_USD,
            "recovery_hours":    RECOVERY_HOURS,
            "triggered_at":      entry.get("triggered_at"),
            "trades_since_reset": entry.get("trades_since_reset", 0),
        }

    def get_all_status(self) -> Dict[str, Dict]:
        """Devuelve el estado de todos los bots conocidos."""
        return {sid: self.get_status(sid) for sid in self._data}

    def manual_reset(self, strategy_id: str) -> bool:
        """Resetea manualmente un circuit breaker (para uso desde la API/frontend)."""
        if strategy_id in self._data:
            self._reset(strategy_id)
            logger.info(f"CircuitBreaker {strategy_id}: reset manual aplicado")
            return True
        return False

    # -----------------------------------------------------------------------
    # Métodos internos
    # -----------------------------------------------------------------------

    def _get_or_create(self, strategy_id: str) -> Dict:
        if strategy_id not in self._data:
            self._data[strategy_id] = {
                "state":             CircuitBreakerState.CLOSED,
                "accumulated_loss":  0.0,
                "triggered_at":      None,
                "trades_since_reset": 0,
            }
        return self._data[strategy_id]

    def _trip(self, strategy_id: str, accumulated_loss: float) -> None:
        """Dispara el circuit breaker — pasa a estado OPEN."""
        entry = self._get_or_create(strategy_id)
        entry["state"]        = CircuitBreakerState.OPEN
        entry["triggered_at"] = datetime.now().isoformat()
        self._save()
        logger.warning(
            f"⛔ CIRCUIT BREAKER DISPARADO: {strategy_id} | "
            f"Pérdida acumulada ${accumulated_loss:.2f} "
            f"(umbral ${abs(MAX_LOSS_USD):.0f}) | "
            f"Bot bloqueado {RECOVERY_HOURS}h"
        )
        # Alerta Telegram
        try:
            from utils.telegram_notifier import send_message
            send_message(
                f"⛔ <b>Circuit Breaker ACTIVADO</b>\n"
                f"Bot: <b>{strategy_id}</b>\n"
                f"Pérdida: <b>${accumulated_loss:.2f}</b> (umbral ${abs(MAX_LOSS_USD):.0f})\n"
                f"Estado: BLOQUEADO por {RECOVERY_HOURS:.0f}h\n"
                f"Recuperación automática en {RECOVERY_HOURS:.0f}h",
                silent=False
            )
        except Exception:
            pass

    def _transition(self, strategy_id: str, new_state: str) -> None:
        entry = self._get_or_create(strategy_id)
        entry["state"] = new_state
        self._save()
        # Alerta Telegram para HALF_OPEN
        if new_state == CircuitBreakerState.HALF_OPEN:
            try:
                from utils.telegram_notifier import send_message
                send_message(
                    f"🟡 <b>Circuit Breaker — Modo prueba</b>\n"
                    f"Bot: <b>{strategy_id}</b>\n"
                    f"Estado: HALF_OPEN — operando en modo prueba\n"
                    f"Primer trade ganador → recuperación completa",
                    silent=True
                )
            except Exception:
                pass

    def _reset(self, strategy_id: str) -> None:
        """Reinicia completamente el estado de un bot."""
        self._data[strategy_id] = {
            "state":              CircuitBreakerState.CLOSED,
            "accumulated_loss":   0.0,
            "triggered_at":       None,
            "trades_since_reset": 0,
        }
        self._save()
        # Alerta Telegram para recuperación completa
        try:
            from utils.telegram_notifier import send_message
            send_message(
                f"✅ <b>Circuit Breaker — Recuperado</b>\n"
                f"Bot: <b>{strategy_id}</b>\n"
                f"Estado: CLOSED — operando normalmente",
                silent=True
            )
        except Exception:
            pass

    def _load(self) -> None:
        """Carga el estado desde disco. Debe llamarse con _data_lock adquirido."""
        if not CB_STATE_FILE.exists():
            logger.info("circuit_breaker_state.json no existe — iniciando con estado limpio")
            return
        try:
            raw = CB_STATE_FILE.read_text(encoding="utf-8").strip()
            if raw:
                self._data = json.loads(raw)
                logger.info(
                    f"CircuitBreaker: estado cargado desde {CB_STATE_FILE} "
                    f"({len(self._data)} entradas)"
                )
        except Exception as e:
            logger.error(f"Error cargando circuit_breaker_state.json: {e} — iniciando con estado limpio")
            self._data = {}

    def _save(self) -> None:
        """Persiste el estado a disco. Usa _data_lock para evitar escrituras concurrentes
        desde múltiples strategy threads que cierran posiciones simultáneamente."""
        with self._data_lock:
            try:
                CB_STATE_FILE.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
            except Exception as e:
                logger.error(f"Error guardando circuit_breaker_state.json: {e}")


# Instancia global — importar desde aquí
circuit_breaker = CircuitBreaker()
