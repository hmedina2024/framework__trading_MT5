"""
Reporte semanal de trading generado con IA (Claude).

Recolecta los trades cerrados de la última semana desde MT5, arma un resumen
de datos por bot, y le pide a la API de Claude que genere un análisis breve
en lenguaje natural. Se envía por Telegram todos los lunes.

Si no hay ANTHROPIC_API_KEY configurada, cae a un reporte con formato fijo
(mismos datos, sin resumen generado por IA) en vez de fallar.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from utils.logger import get_logger

logger = get_logger(__name__)

try:
    from utils.telegram_notifier import send_message
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.info("Paquete 'anthropic' no instalado — reporte semanal sin resumen IA (fallback fijo)")

# Reverse map de magic number -> tipo de estrategia. Coincide con
# STRATEGY_MAGIC_BASE (trading_service.py) y _MAGIC_BASE_TO_STRATEGY
# (analysis.py) — se duplica aquí en vez de importar entre capas api/core
# por una constante tan estable; mantener sincronizada si cambia.
_MAGIC_BASE_TO_STRATEGY = {
    210000: 'MA_CROSS',  220000: 'RSI',        230000: 'BOLLINGER',
    240000: 'MACD',      250000: 'BREAKOUT',   260000: 'SUPERTREND',
    270000: 'EMA_CROSS', 280000: 'WILLIAMS_R', 300000: 'LONDON_ORB',
    310000: 'FVG',       320000: 'NY_ORB',
}

MODEL             = "claude-haiku-4-5-20251001"  # barato, sobra para resumir datos estructurados
REPORT_DAY_UTC    = 0   # 0 = lunes
REPORT_HOUR_UTC   = 8   # 08:00 UTC


def _gather_weekly_data(connector) -> dict:
    """Reúne los trades cerrados de los últimos 7 días, agrupados por bot."""
    date_from = datetime.now() - timedelta(days=7)
    trades = connector.get_closed_trades(from_date=date_from)

    by_bot = {}
    total_pnl = 0.0
    for t in trades:
        base = (t['magic'] // 10000) * 10000
        strategy_type = _MAGIC_BASE_TO_STRATEGY.get(base)
        if not strategy_type:
            continue  # trade manual u otro EA
        bot_id = f"{strategy_type}_{t['symbol']}"
        b = by_bot.setdefault(bot_id, {'wins': 0, 'losses': 0, 'pnl': 0.0})
        won = t['profit'] >= 0
        b['wins' if won else 'losses'] += 1
        b['pnl'] += t['profit']
        total_pnl += t['profit']

    return {
        'trades_count':  len(trades),
        'total_pnl':     round(total_pnl, 2),
        'by_bot':        by_bot,
        'period_start':  date_from.strftime('%Y-%m-%d'),
        'period_end':    datetime.now().strftime('%Y-%m-%d'),
    }


def _format_bots_lines(by_bot: dict) -> str:
    rows = sorted(by_bot.items(), key=lambda x: -x[1]['pnl'])
    return "\n".join(
        f"- {bot}: {b['wins']}W/{b['losses']}L, P&L ${b['pnl']:+.2f}"
        for bot, b in rows
    ) or "(sin trades esta semana)"


def _generate_ai_summary(data: dict, balance: float) -> str:
    """Le pide a Claude un resumen breve de la semana. None si no hay API key o falla."""
    if not _ANTHROPIC_AVAILABLE:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY no configurada — reporte semanal usa formato fijo")
        return None

    prompt = f"""Eres un analista de trading algorítmico. Genera un resumen breve
(máximo 150 palabras, en español, tono directo y honesto, sin adornos) de la
semana de trading de un sistema automatizado en MetaTrader 5, para enviar
por Telegram.

Periodo: {data['period_start']} a {data['period_end']}
Balance actual: ${balance:.2f}
Trades cerrados: {data['trades_count']}
P&L total de la semana: ${data['total_pnl']:+.2f}

Por bot (estrategia_símbolo: ganados/perdidos, P&L):
{_format_bots_lines(data['by_bot'])}

Destaca: el/los mejor(es) y peor(es) bot(s), cualquier patrón preocupante
(ej. una estrategia perdiendo consistentemente en varios símbolos), y una
recomendación concreta de qué vigilar la próxima semana. No inventes datos
que no están aquí. Si hay muy pocos trades, dilo explícitamente en vez de
sacar conclusiones de una muestra chica."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Error generando resumen semanal con IA: {e}")
        return None


def generate_and_send_report(connector) -> None:
    """Genera el reporte semanal (con IA si está disponible) y lo envía por Telegram."""
    try:
        data = _gather_weekly_data(connector)
        account_info = connector.get_account_info()
        if account_info is None:
            logger.warning("No se pudo obtener info de cuenta para el reporte semanal")
            return

        summary = _generate_ai_summary(data, account_info.balance)

        if summary:
            message = (
                f"📊 <b>Reporte semanal</b> ({data['period_start']} → {data['period_end']})\n\n"
                f"{summary}"
            )
        else:
            lines = [
                f"📊 <b>Reporte semanal</b> ({data['period_start']} → {data['period_end']})",
                f"Balance: ${account_info.balance:.2f}",
                f"Trades: {data['trades_count']} | P&L semana: ${data['total_pnl']:+.2f}",
                "",
                _format_bots_lines(data['by_bot']),
            ]
            message = "\n".join(lines)

        if _TELEGRAM_AVAILABLE:
            send_message(message, silent=False)
            logger.info("Reporte semanal enviado por Telegram")
        else:
            logger.warning("Telegram no disponible — reporte semanal generado pero no enviado")

        logger.info(
            f"Reporte semanal: {data['trades_count']} trades, "
            f"P&L ${data['total_pnl']:+.2f}, resumen IA: {'sí' if summary else 'no'}"
        )

    except Exception as e:
        logger.error(f"Error generando reporte semanal: {e}", exc_info=True)


class WeeklyReportScheduler:
    """Dispara generate_and_send_report() una vez por semana (lunes REPORT_HOUR_UTC:00 UTC)."""

    def __init__(self, connector):
        self.connector = connector
        self._running = False
        self._thread = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="WeeklyReportScheduler"
        )
        self._thread.start()
        logger.info(
            f"WeeklyReportScheduler iniciado (lunes {REPORT_HOUR_UTC:02d}:00 UTC)"
        )

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        last_sent_week = None
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if now.weekday() == REPORT_DAY_UTC and now.hour == REPORT_HOUR_UTC:
                    week_key = now.strftime('%Y-%W')
                    if week_key != last_sent_week:
                        logger.info("Generando reporte semanal...")
                        generate_and_send_report(self.connector)
                        last_sent_week = week_key
                time.sleep(60)
            except Exception as e:
                logger.error(f"Error en WeeklyReportScheduler: {e}")
                time.sleep(60)
