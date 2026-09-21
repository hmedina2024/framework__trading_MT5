"""
Registro de predicciones del filtro de sesgo por IA vs. lo que hizo el precio
después — no influye en el trading, es un tracker paralelo para acumular
evidencia de si el sesgo de la IA acierta más de lo que falla, antes de
considerar usarlo como algo más que un veto (ver discusión en el chat:
hoy solo bloquea entradas, la idea es medir su precisión antes de dejarlo
confirmar o generar entradas).

Cada vez que news_sentiment.py calcula un sesgo FRESCO (no desde cache) para
una divisa, se registra el precio del par proxy en ese momento. Un scheduler
en segundo plano revisa cada 30 min si alguna predicción ya cumplió su
horizonte de evaluación (EVAL_HORIZON_HOURS) y la resuelve comparando el
precio del par proxy contra el de cuando se predijo.
"""
import json
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

PREDICTIONS_FILE       = Path("news_predictions.json")
EVAL_HORIZON_HOURS      = 6      # tiempo tras la predicción para medir el resultado
FLAT_THRESHOLD_PCT      = 0.001  # movimiento del par proxy por debajo de esto = "sin cambio" (NEUTRAL)
RESOLVE_INTERVAL_SECONDS = 1800  # cada cuánto revisa predicciones pendientes

# divisa -> (par proxy, es_divisa_base_del_par).
# Si es la base (True), precio del par sube = la divisa se fortalece.
# Si es la cotizada (False), precio del par sube = la divisa se DEBILITA (hay que invertir el signo).
PROXY = {
    'USD': ('EURUSD', False),
    'EUR': ('EURUSD', True),
    'GBP': ('GBPUSD', True),
    'JPY': ('USDJPY', False),
    'CAD': ('USDCAD', False),
    'AUD': ('AUDUSD', True),
    'NZD': ('NZDUSD', True),
    'CHF': ('USDCHF', False),
}


class NewsPredictionTracker:
    """Singleton — persiste en PREDICTIONS_FILE, sobrevive reinicios del servidor."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._data_lock = threading.RLock()
                inst._predictions = inst._load()
                inst._connector = None
                inst._running = False
                inst._thread = None
                cls._instance = inst
        return cls._instance

    def _load(self):
        if PREDICTIONS_FILE.exists():
            try:
                return json.loads(PREDICTIONS_FILE.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                logger.warning("news_predictions.json corrupto — arrancando vacío")
        return []

    def _save(self):
        try:
            PREDICTIONS_FILE.write_text(
                json.dumps(self._predictions, indent=2, ensure_ascii=False), encoding='utf-8'
            )
        except OSError as e:
            logger.error(f"No se pudo guardar news_predictions.json: {e}")

    def record(self, currency: str, bias: str, reasoning: str) -> None:
        """Registra una predicción nueva con el precio actual del par proxy."""
        if not self._connector:
            return
        proxy = PROXY.get(currency)
        if not proxy:
            return
        symbol, is_base = proxy
        try:
            market_data = self._connector.get_market_data(symbol)
        except Exception as e:
            logger.debug(f"No se pudo registrar predicción {currency}: {e}")
            return
        if not market_data or not market_data.bid or not market_data.ask:
            return
        price = (market_data.bid + market_data.ask) / 2
        now = datetime.now(timezone.utc)
        with self._data_lock:
            self._predictions.append({
                'currency':            currency,
                'bias':                bias,
                'reasoning':           reasoning,
                'proxy_symbol':        symbol,
                'is_base':             is_base,
                'price_at_prediction': price,
                'predicted_at':        now.isoformat(),
                'eval_at':             (now + timedelta(hours=EVAL_HORIZON_HOURS)).isoformat(),
                'resolved':            False,
                'actual':              None,
                'correct':             None,
                'price_at_eval':       None,
                'move_pct':            None,
            })
            self._save()
        logger.info(
            f"NewsPredictionTracker: registrada {currency}={bias} "
            f"(proxy {symbol}={price:.5f}, se evalúa en {EVAL_HORIZON_HOURS}h)"
        )

    def resolve_due(self) -> int:
        """Resuelve predicciones cuyo horizonte de evaluación ya pasó. Devuelve cuántas resolvió."""
        if not self._connector:
            return 0
        now = datetime.now(timezone.utc)
        resolved_count = 0
        with self._data_lock:
            for p in self._predictions:
                if p['resolved']:
                    continue
                try:
                    if now < datetime.fromisoformat(p['eval_at']):
                        continue
                except ValueError:
                    continue
                try:
                    market_data = self._connector.get_market_data(p['proxy_symbol'])
                except Exception:
                    continue
                if not market_data or not market_data.bid or not market_data.ask:
                    continue
                price_now = (market_data.bid + market_data.ask) / 2
                raw_move_pct = (price_now - p['price_at_prediction']) / p['price_at_prediction']
                currency_move_pct = raw_move_pct if p['is_base'] else -raw_move_pct

                if abs(currency_move_pct) < FLAT_THRESHOLD_PCT:
                    actual = 'NEUTRAL'
                else:
                    actual = 'BULLISH' if currency_move_pct > 0 else 'BEARISH'

                p['price_at_eval'] = price_now
                p['move_pct']      = round(currency_move_pct * 100, 4)
                p['actual']        = actual
                p['correct']       = (actual == p['bias'])
                p['resolved']      = True
                resolved_count += 1
                logger.info(
                    f"NewsPredictionTracker: {p['currency']} predijo {p['bias']}, "
                    f"mercado hizo {actual} ({p['move_pct']:+.3f}%) — "
                    f"{'ACIERTO' if p['correct'] else 'FALLO'}"
                )
            if resolved_count:
                self._save()
        return resolved_count

    def get_stats(self) -> dict:
        with self._data_lock:
            resolved = [p for p in self._predictions if p['resolved']]
            pending  = sum(1 for p in self._predictions if not p['resolved'])
        total = len(resolved)
        if total == 0:
            return {'total_resolved': 0, 'pending': pending, 'accuracy': None, 'by_bias': {}}
        correct = sum(1 for p in resolved if p['correct'])
        by_bias = {}
        for p in resolved:
            b = by_bias.setdefault(p['bias'], {'total': 0, 'correct': 0})
            b['total'] += 1
            b['correct'] += int(p['correct'])
        for b in by_bias.values():
            b['accuracy'] = round(b['correct'] / b['total'], 4)
        return {
            'total_resolved': total,
            'pending':        pending,
            'correct':        correct,
            'accuracy':       round(correct / total, 4),
            'by_bias':        by_bias,
        }

    def start_scheduler(self, connector) -> None:
        self._connector = connector
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NewsPredictionTracker")
        self._thread.start()
        logger.info(
            f"NewsPredictionTracker iniciado (horizonte {EVAL_HORIZON_HOURS}h, "
            f"revisa cada {RESOLVE_INTERVAL_SECONDS // 60} min)"
        )

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.resolve_due()
            except Exception as e:
                logger.error(f"Error en NewsPredictionTracker: {e}")
            for _ in range(RESOLVE_INTERVAL_SECONDS):
                if not self._running:
                    break
                time.sleep(1)


news_prediction_tracker = NewsPredictionTracker()
