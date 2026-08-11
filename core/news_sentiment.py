"""
Filtro de sesgo fundamental por IA — lee el calendario de noticias de alto
impacto (el mismo que ya usa el blackout binario en strategy_base) y le pide
a Claude que interprete el sesgo direccional real de cada divisa afectada.

Es puramente defensivo: solo puede VETAR una entrada cuya dirección
contradiga el sesgo fundamental reciente, nunca genera ni sugiere entradas
por sí mismo. Si falla la llamada a la API, no hay eventos recientes, o no
hay ANTHROPIC_API_KEY configurada, no bloquea nada (fail-open) — el sistema
sigue funcionando exactamente igual que sin este filtro.

Costo controlado: solo llama a la API cuando hay eventos de alto impacto
recientes para una divisa (la mayoría de las veces no los hay), y cachea el
resultado por divisa durante NEWS_SENTIMENT_CACHE_MINUTES — no se re-evalúa
en cada iteración de cada bot.
"""
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

MODEL                          = "claude-haiku-4-5-20251001"
NEWS_SENTIMENT_CACHE_MINUTES   = 60     # igual que el cache del calendario en strategy_base
RELEVANT_EVENT_WINDOW_HOURS    = 24     # considera eventos de las ultimas 24h (no solo el blackout de 30min)

# Símbolo -> (divisa base, divisa cotizada). None = sin divisa fiat relevante
# (cripto) o el símbolo no tiene contraparte fiat clara para el sesgo.
SYMBOL_CURRENCIES: Dict[str, Tuple[Optional[str], Optional[str]]] = {
    'EURUSD': ('EUR', 'USD'),
    'GBPUSD': ('GBP', 'USD'),
    'USDJPY': ('USD', 'JPY'),
    'USDCAD': ('USD', 'CAD'),
    'AUDUSD': ('AUD', 'USD'),
    'XAUUSD': (None, 'USD'),   # oro cotiza contra USD
    'US30':   (None, 'USD'),   # índice USD
    'BTCUSD': (None, None),    # cripto — sin divisa fiat de referencia clara
}

# País (formato ForexFactory) -> código de divisa
_COUNTRY_TO_CURRENCY = {
    'USD': 'USD', 'EUR': 'EUR', 'GBP': 'GBP', 'JPY': 'JPY',
    'CAD': 'CAD', 'AUD': 'AUD', 'NZD': 'NZD', 'CHF': 'CHF',
}


class NewsSentimentFilter:
    """Singleton — cachea el sesgo por divisa para no llamar a la API en cada iteración."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._cache: Dict[str, Dict] = {}   # currency -> {'bias','reasoning','cached_at'}
                inst._data_lock = threading.RLock()
                cls._instance = inst
        return cls._instance

    def _get_currency_bias(self, currency: str, events: List[Dict]) -> Optional[Dict]:
        """
        Retorna {'bias': 'BULLISH'|'BEARISH'|'NEUTRAL', 'reasoning': str} para
        la divisa, o None si no hay eventos recientes relevantes o falla.
        Usa cache — no vuelve a llamar a la API dentro de la misma ventana.
        """
        with self._data_lock:
            cached = self._cache.get(currency)
            if cached and (datetime.now(timezone.utc) - cached['cached_at']).total_seconds() < NEWS_SENTIMENT_CACHE_MINUTES * 60:
                return cached

        if not _ANTHROPIC_AVAILABLE:
            return None
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        # Filtrar eventos recientes/próximos relevantes para esta divisa
        now_utc = datetime.now(timezone.utc)
        relevant = []
        for e in events:
            if _COUNTRY_TO_CURRENCY.get(e.get('country', '')) != currency:
                continue
            date_str, time_str = e.get('date', ''), e.get('time', '')
            if not date_str or not time_str:
                continue
            try:
                event_dt = datetime.strptime(
                    f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            hours_diff = (now_utc - event_dt).total_seconds() / 3600
            if -2 <= hours_diff <= RELEVANT_EVENT_WINDOW_HOURS:  # ya pasó (hasta 24h) o es inminente (hasta 2h antes)
                relevant.append(e.get('title', ''))

        if not relevant:
            return None  # sin eventos relevantes recientes — no vale la pena llamar a la API

        prompt = f"""Eres un analista forex. Con base ÚNICAMENTE en estos eventos
económicos de alto impacto recientes/próximos para {currency}, responde en
UNA sola línea con este formato exacto:
SESGO: [BULLISH|BEARISH|NEUTRAL] | RAZON: [máximo 15 palabras]

Eventos:
{chr(10).join(f'- {t}' for t in relevant)}

Si los eventos son mixtos, no hay dirección clara, o ya son ambiguos, usa NEUTRAL."""

        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=MODEL, max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            bias = 'NEUTRAL'
            for b in ('BULLISH', 'BEARISH', 'NEUTRAL'):
                if b in text.upper():
                    bias = b
                    break
            result = {'bias': bias, 'reasoning': text, 'cached_at': datetime.now(timezone.utc)}
            with self._data_lock:
                self._cache[currency] = result
            logger.info(f"Sesgo fundamental {currency}: {bias} — {text}")
            return result
        except Exception as e:
            logger.debug(f"NewsSentimentFilter: error consultando IA para {currency}: {e}")
            return None

    def should_block_entry(self, symbol: str, direction: str, events: List[Dict]) -> Tuple[bool, str]:
        """
        (should_block, reason). Solo bloquea si el sesgo fundamental de alguna
        divisa relevante CONTRADICE claramente la dirección de la señal.
        Fail-open ante cualquier ambigüedad, error, o falta de configuración.
        """
        base, quote = SYMBOL_CURRENCIES.get(symbol, (None, None))
        if base is None and quote is None:
            return False, ""

        try:
            # BUY del símbolo = largo base / corto quote (o largo del activo
            # si no hay base, ej. XAUUSD, US30 — se interpreta contra 'quote').
            for currency, wants_bullish_currency in (
                (base,  direction == 'BUY'),    # BUY EURUSD = quiere EUR alcista
                (quote, direction == 'SELL'),   # BUY EURUSD = quiere USD bajista (SELL en USD)
            ):
                if not currency:
                    continue
                info = self._get_currency_bias(currency, events)
                if not info or info['bias'] == 'NEUTRAL':
                    continue
                currency_is_bullish = info['bias'] == 'BULLISH'
                if currency_is_bullish != wants_bullish_currency:
                    return True, (
                        f"sesgo fundamental {currency} {info['bias']} contradice "
                        f"{direction} en {symbol} — {info['reasoning']}"
                    )
            return False, ""
        except Exception as e:
            logger.debug(f"NewsSentimentFilter.should_block_entry error (ignorado): {e}")
            return False, ""


news_sentiment_filter = NewsSentimentFilter()
