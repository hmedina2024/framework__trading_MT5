"""
Reconciliación periódica de stats_{TIPO}_{SIMBOLO}.json contra el historial
real de MT5 (fuente autoritativa).

Motivación (incidente 2026-10-06): cuando el RegimeDetector detiene un bot
(stop() → is_running=False, el hilo termina) mientras ese bot todavía tenía
una posición abierta, nadie vuelve a llamar on_trade_closed() cuando esa
posición cierra más tarde (SL/TP) — el trade pasó de verdad, movió dinero
real, pero trades_count/wins/losses del bot nunca se actualiza. Confirmado
con SUPERTREND_BTCUSD: detenido 2026-10-06 11:04 por cambio de régimen,
posición cerrada esa misma noche (-$25.91) con CERO bots corriendo para
registrarlo — stats_SUPERTREND_BTCUSD.json quedó congelado desde el
2026-09-10 pese al trade real.

Esta misma lógica ya existía como endpoint manual (POST /analysis/stats-
backfill, pensado originalmente para arrancar con datos reales en una
cuenta nueva). Se extrae aquí para reutilizarla también desde un scheduler
automático — así el hueco se autocorrige cada pocas horas en vez de
depender de que alguien lo note y lo corra a mano.

Reescribe stats_*.json por completo desde el historial MT5 (idempotente,
no acumula sobre lo que ya había en disco) — por diseño, esto también
purga cualquier stat contaminado por una cuenta MT5 anterior (ver
incidente 2026-09-21: cambio de cuenta demo dejó mezclados trades de la
cuenta vieja con la nueva en los mismos archivos de stats).
"""
import json
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta

from utils.logger import get_logger

logger = get_logger(__name__)

RECONCILE_INTERVAL_SECONDS = 4 * 3600   # mismo cadencia que el ciclo del RegimeDetector
RECONCILE_LOOKBACK_DAYS    = 90         # suficiente para cubrir la vida de una cuenta demo típica

_MAGIC_BASE_TO_STRATEGY = {
    210000: 'MA_CROSS',  220000: 'RSI',        230000: 'BOLLINGER',
    240000: 'MACD',      250000: 'BREAKOUT',   260000: 'SUPERTREND',
    270000: 'EMA_CROSS', 280000: 'WILLIAMS_R', 300000: 'LONDON_ORB',
    310000: 'FVG',       320000: 'NY_ORB',
}


def reconcile_stats(connector, days: int = RECONCILE_LOOKBACK_DAYS) -> dict:
    """
    Reconstruye stats_{TIPO}_{SIMBOLO}.json desde el historial real de MT5.
    Devuelve un resumen (mismo formato que usaba el endpoint stats-backfill).
    Lanza excepción si no hay conexión o falla la consulta — quien llame
    decide si lo registra como warning (scheduler) o como error HTTP (API).
    """
    from strategies.strategy_base import ROLLING_WINDOW_SIZE

    if connector is None or not connector.is_connected():
        raise RuntimeError("MT5 no está conectado")

    date_from = datetime.now() - timedelta(days=days)
    trades = connector.get_closed_trades(from_date=date_from)
    if not trades:
        return {"archivos": 0, "trades_evaluados": 0, "descartados_ajenos": 0,
                "rango_dias": days, "bots": []}

    by_bot = {}
    skipped_foreign = 0
    for t in trades:
        base = (t['magic'] // 10000) * 10000
        strategy_type = _MAGIC_BASE_TO_STRATEGY.get(base)
        if not strategy_type:
            skipped_foreign += 1
            continue
        strategy_id = f"{strategy_type}_{t['symbol']}"
        bucket = by_bot.setdefault(strategy_id, {'wins': 0, 'losses': 0, 'results': []})
        won = t['profit'] >= 0
        bucket['wins' if won else 'losses'] += 1
        bucket['results'].append(1 if won else 0)

    written = []
    for strategy_id, b in by_bot.items():
        total = b['wins'] + b['losses']
        payload = {
            "strategy_id":    strategy_id,
            "trades_count":   total,
            "wins":           b['wins'],
            "losses":         b['losses'],
            "avg_rr":         0.0,
            "recent_results": b['results'][-ROLLING_WINDOW_SIZE:],
            "last_updated":   datetime.now().isoformat(),
            "source":         "mt5_stats_reconciler",
        }
        Path(f"stats_{strategy_id}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        wr = round(b['wins'] / total * 100, 1) if total else 0.0
        written.append({"bot": strategy_id, "trades": total,
                        "wins": b['wins'], "losses": b['losses'], "wr_pct": wr})

    written.sort(key=lambda x: (x['wr_pct'], -x['trades']))
    return {
        "archivos":           len(written),
        "trades_evaluados":   len(trades),
        "descartados_ajenos": skipped_foreign,
        "rango_dias":         days,
        "bots":               written,
    }


class StatsReconcilerScheduler:
    """Corre reconcile_stats() cada RECONCILE_INTERVAL_SECONDS en segundo plano."""

    def __init__(self, connector):
        self.connector = connector
        self._running = False
        self._thread = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="StatsReconciler")
        self._thread.start()
        logger.info(
            f"StatsReconciler iniciado (cada {RECONCILE_INTERVAL_SECONDS // 3600}h)"
        )

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                result = reconcile_stats(self.connector)
                logger.info(
                    f"StatsReconciler: {result['archivos']} archivos reconciliados "
                    f"({result['trades_evaluados']} trades evaluados)"
                )
            except Exception as e:
                logger.warning(f"StatsReconciler: no se pudo reconciliar: {e}")
            for _ in range(RECONCILE_INTERVAL_SECONDS):
                if not self._running:
                    break
                time.sleep(1)
