"""
Módulo de alertas por Telegram para el framework de trading MT5.

Envía notificaciones cuando:
  - Se abre una posición (estrategia, símbolo, dirección, precio, SL, TP)
  - Se cierra una posición (profit/loss, razón de cierre)
  - El balance cae más de ALERT_BALANCE_DROP_PCT en el día
  - Un bot lleva más de ALERT_BOT_SILENT_HOURS sin operar
  - El servidor arranca o se reinicia

Configuración via variables de entorno (.env):
  TELEGRAM_BOT_TOKEN = 7234567890:AAHxxxxxxxxxxxxxxxxxxxxx
  TELEGRAM_CHAT_ID   = 123456789

Si las variables no están definidas, el módulo se desactiva silenciosamente.
"""
import os
import urllib.request
import urllib.parse
import json
import threading
from datetime import datetime, timezone
from typing import Optional
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuración — leer desde .env
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')

# Alertas adicionales
ALERT_BALANCE_DROP_PCT  = 0.03   # alertar si balance cae 3% en el día
ALERT_BOT_SILENT_HOURS  = 24     # alertar si un bot no opera en 24h

# ---------------------------------------------------------------------------
# Cliente Telegram
# ---------------------------------------------------------------------------

def _is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_message(text: str, silent: bool = False) -> bool:
    """
    Envía un mensaje de texto a Telegram.
    silent=True envía sin sonido (útil para notificaciones informativas).
    Retorna True si se envió correctamente, False en caso de error.
    El envío es asíncrono — no bloquea el hilo principal.
    """
    if not _is_configured():
        return False

    def _send():
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

            payload = json.dumps({
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text,
                'parse_mode': 'HTML',
                'disable_notification': silent,
            }).encode('utf-8')

            req = urllib.request.Request(
                url,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            # ✅ SOLUCIÓN SSL
            import ssl
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())

            with urllib.request.urlopen(req, timeout=5, context=context) as resp:
                result = json.loads(resp.read())
                if not result.get('ok'):
                    logger.warning(f"Telegram API error: {result}")

        except Exception as e:
            logger.warning(f"No se pudo enviar alerta Telegram: {e}")

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True


# ---------------------------------------------------------------------------
# Alertas específicas de trading
# ---------------------------------------------------------------------------

def alert_trade_opened(strategy: str, symbol: str, direction: str,
                       entry: float, sl: float, tp: float,
                       volume: float, risk_pct: float) -> None:
    """Alerta cuando se abre una nueva posición."""
    emoji = '🟢' if direction == 'BUY' else '🔴'
    rr = abs(tp - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
    now = datetime.now().strftime('%H:%M')

    text = (
        f"{emoji} <b>NUEVA POSICIÓN — {direction}</b>\n"
        f"🕐 {now} | 📊 {strategy}\n"
        f"💱 {symbol} | {volume:.2f} lotes\n"
        f"📍 Entrada: <code>{entry:.5f}</code>\n"
        f"🛑 SL: <code>{sl:.5f}</code>\n"
        f"🎯 TP: <code>{tp:.5f}</code>\n"
        f"📐 R:R 1:{rr:.2f} | Riesgo: {risk_pct*100:.1f}%"
    )
    send_message(text)


def alert_trade_closed(strategy: str, symbol: str, direction: str,
                       profit: float, reason: str = '') -> None:
    """Alerta cuando se cierra una posición con su resultado."""
    # Distinguir tres casos: ganancia, pérdida, o datos no disponibles aún
    if profit > 0.001:
        emoji  = '✅'
        result = f"+${profit:.2f}"
    elif profit < -0.001:
        emoji  = '❌'
        result = f"-${abs(profit):.2f}"
    else:
        # profit == 0 puede significar que el deal aún no está en el historial
        # o que realmente fue breakeven — mostrar como pendiente
        emoji  = '⏳'
        result = "dato pendiente (breakeven o historial aún no disponible)"

    now = datetime.now().strftime('%H:%M')
    reason_line = f"\n📝 Razón: {reason}" if reason else ''

    text = (
        f"{emoji} <b>POSICIÓN CERRADA — {result}</b>\n"
        f"🕐 {now} | 📊 {strategy}\n"
        f"💱 {symbol} {direction}{reason_line}"
    )
    send_message(text)


def alert_balance_drop(balance_start: float, balance_now: float) -> None:
    """Alerta si el balance cae más de ALERT_BALANCE_DROP_PCT en el día."""
    drop_pct = (balance_start - balance_now) / balance_start * 100
    text = (
        f"⚠️ <b>ALERTA DE BALANCE</b>\n"
        f"Balance inicial hoy: ${balance_start:.2f}\n"
        f"Balance actual: ${balance_now:.2f}\n"
        f"Caída: -{drop_pct:.2f}%"
    )
    send_message(text)


def alert_server_start(bots_launched: int) -> None:
    """Alerta cuando el servidor arranca."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    text = (
        f"🚀 <b>Servidor iniciado</b>\n"
        f"🕐 {now}\n"
        f"🤖 Bots auto-arrancados: {bots_launched}"
    )
    send_message(text, silent=True)


def alert_bot_silent(strategy: str, symbol: str, hours: float) -> None:
    """Alerta si un bot lleva demasiadas horas sin operar."""
    text = (
        f"💤 <b>Bot inactivo</b>\n"
        f"📊 {strategy} — {symbol}\n"
        f"⏱ Sin operar hace {hours:.0f}h\n"
        f"Posible bug o mercado sin señales."
    )
    send_message(text, silent=True)


def alert_news_blackout(event_title: str, country: str, minutes_to: int) -> None:
    """Alerta cuando se activa el blackout por noticias."""
    text = (
        f"📰 <b>Blackout por noticias</b>\n"
        f"📊 {event_title} ({country})\n"
        f"⏱ En {minutes_to} minutos — bots pausados"
    )
    send_message(text, silent=True)