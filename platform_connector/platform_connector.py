"""
Conector mejorado para MetaTrader5 con manejo robusto de errores
"""
import MetaTrader5 as mt5
import threading
import time
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd

from config.settings import settings
from utils.logger import get_logger
from models.trade_models import (
    AccountInfo, Position, MarketData, SymbolInfo, TradeResult
)

logger = get_logger(__name__)

class MT5Error(Exception):
    """Excepción personalizada para errores de MT5"""
    pass

class PlatformConnector:
    """
    Conector robusto para MetaTrader5 con gestión completa de conexión,
    manejo de errores y logging detallado.
    """
    
    def __init__(self, auto_connect: bool = True):
        """
        Inicializa el conector de MT5

        Args:
            auto_connect: Si debe conectarse automáticamente al inicializar
        """
        self._connected = False
        self._account_info = None
        # RLock (reentrant) para serializar todas las llamadas a MT5.
        # La librería MetaTrader5 no es thread-safe: múltiples hilos llamando
        # copy_rates_from_pos/positions_get/etc simultáneamente puede causar
        # crashes o datos corruptos. El RLock permite que el mismo hilo adquiera
        # el lock recursivamente (ej: ensure_connection → connect → mt5.initialize).
        self._mt5_lock = threading.RLock()
        logger.info("Inicializando PlatformConnector")

        if auto_connect:
            self.connect()
    
    def connect(self) -> bool:
        """
        Establece conexión con MT5.

        Returns:
            True si la conexión fue exitosa, False en caso contrario

        Raises:
            MT5Error: Si hay un error crítico en la conexión
        """
        with self._mt5_lock:
            if self._connected:
                logger.warning("Ya existe una conexión activa con MT5")
                return True

            if not settings.validate():
                logger.error("Configuración inválida. Revisa el archivo .env")
                return False

            try:
                logger.info("Intentando conectar a MT5...")
                logger.debug(f"Path: {settings.MT5_PATH}")
                logger.debug(f"Server: {settings.MT5_SERVER}")
                logger.debug(f"Login: {settings.MT5_LOGIN}")

                # Estrategia 0: engancharse a una terminal YA abierta y logueada,
                # sin credenciales explícitas. Evita el error "Terminal: Authorization
                # failed" que ocurre cuando se fuerza un re-login explícito sobre una
                # sesión que ya está autenticada (ej. reconexión tras un hipo
                # transitorio con muchos bots golpeando la API concurrentemente).
                logger.info("Estrategia 0: Enganchando a terminal ya abierto (sin credenciales)...")
                initialized = mt5.initialize()
                if initialized:
                    acc = mt5.account_info()
                    if acc is None or acc.login != settings.MT5_LOGIN:
                        # Terminal abierto pero en otra cuenta (o sin sesión válida)
                        initialized = False
                        mt5.shutdown()

                if not initialized:
                    # Estrategia 1: login explícito (primera vez, o terminal cerrado/
                    # en otra cuenta)
                    logger.info("Estrategia 1: Conectando con credenciales explícitas...")
                    initialized = mt5.initialize(
                        login=settings.MT5_LOGIN,
                        password=settings.MT5_PASSWORD,
                        server=settings.MT5_SERVER,
                        timeout=settings.MT5_TIMEOUT
                    )

                if not initialized:
                    logger.warning(f"Estrategia 1 falló: {mt5.last_error()}, intentando con path...")
                    mt5.shutdown()

                    # Estrategia 2: Lanzar nueva instancia con path completo
                    logger.info("Estrategia 2: Lanzando MT5 con path completo...")
                    initialized = mt5.initialize(
                        path=settings.MT5_PATH,
                        login=settings.MT5_LOGIN,
                        password=settings.MT5_PASSWORD,
                        server=settings.MT5_SERVER,
                        timeout=settings.MT5_TIMEOUT,
                        portable=settings.MT5_PORTABLE
                    )

                    if not initialized:
                        error = mt5.last_error()
                        logger.error(f"Error al inicializar MT5: {error}")
                        raise MT5Error(f"Fallo en inicialización: {error}")

                account_info = mt5.account_info()
                if account_info is None:
                    error = mt5.last_error()
                    logger.error(f"No se pudo obtener información de cuenta: {error}")
                    mt5.shutdown()
                    raise MT5Error(f"Fallo al obtener info de cuenta: {error}")

                self._connected = True
                self._account_info = account_info

                logger.info("✅ Conexión exitosa a MT5")
                logger.info(f"Cuenta: {account_info.login}")
                logger.info(f"Servidor: {account_info.server}")
                logger.info(f"Balance: {account_info.balance} {account_info.currency}")

                return True

            except Exception as e:
                logger.error(f"Error inesperado al conectar: {str(e)}", exc_info=True)
                self._connected = False
                return False

    def disconnect(self) -> None:
        """Cierra la conexión con MT5"""
        with self._mt5_lock:
            if self._connected:
                mt5.shutdown()
                self._connected = False
                logger.info("Desconectado de MT5")
            else:
                logger.warning("No hay conexión activa para cerrar")

    def is_connected(self) -> bool:
        """Verifica si hay conexión activa con MT5.
        Comprueba el flag interno Y el estado real del terminal para detectar
        desconexiones externas (crash de MT5, timeout de red, etc.).

        Reintenta un par de veces antes de declarar desconexión: con muchos
        bots (hilos) golpeando la API de MT5 concurrentemente, un solo
        mt5.account_info() puede devolver None de forma transitoria sin que
        la conexión real esté rota. Marcar _connected=False en ese momento es
        costoso: el siguiente ensure_connection() dispara connect(), que
        reintenta un login EXPLÍCITO sobre una terminal que ya está
        autenticada — y eso sí falla de verdad ("Terminal: Authorization
        failed"), dejando la app marcada como desconectada aunque el
        terminal (y los bots, que llaman a MT5 por otras rutas) sigan
        funcionando perfectamente.
        """
        if not self._connected:
            return False
        with self._mt5_lock:
            for attempt in range(3):
                try:
                    info = mt5.account_info()
                    if info is not None:
                        return True
                    logger.debug(f"is_connected intento {attempt}: account_info=None, last_error={mt5.last_error()}")
                except Exception as e:
                    logger.debug(f"is_connected intento {attempt}: excepcion {e!r}")
                if attempt < 2:
                    time.sleep(0.3)
            logger.warning("MT5 desconectado externamente — marcando para reconexión")
            self._connected = False
            return False

    def ensure_connection(self) -> bool:
        """
        Asegura que haya una conexión activa, reconectando si es necesario.

        Returns:
            True si hay conexión, False en caso contrario
        """
        if not self.is_connected():
            logger.warning("Conexión perdida, intentando reconectar...")
            return self.connect()
        return True
    
    def get_account_info(self) -> Optional[AccountInfo]:
        """
        Obtiene información de la cuenta

        Returns:
            AccountInfo con los datos de la cuenta o None si hay error
        """
        if not self.ensure_connection():
            return None

        with self._mt5_lock:
            try:
                account = mt5.account_info()
                if account is None:
                    logger.error(f"Error al obtener info de cuenta: {mt5.last_error()}")
                    return None

                return AccountInfo(
                    login=account.login,
                    balance=account.balance,
                    equity=account.equity,
                    profit=account.profit,
                    margin=account.margin,
                    margin_free=account.margin_free,
                    margin_level=account.margin_level if account.margin_level else 0.0,
                    leverage=account.leverage,
                    currency=account.currency,
                    server=account.server,
                    company=account.company
                )
            except Exception as e:
                logger.error(f"Error al procesar info de cuenta: {str(e)}", exc_info=True)
                return None
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Obtiene las posiciones abiertas

        Args:
            symbol: Filtrar por símbolo específico (opcional)

        Returns:
            Lista de posiciones abiertas
        """
        if not self.ensure_connection():
            return []

        with self._mt5_lock:
            try:
                if symbol:
                    positions = mt5.positions_get(symbol=symbol)
                else:
                    positions = mt5.positions_get()

                if positions is None:
                    logger.warning(f"No se pudieron obtener posiciones: {mt5.last_error()}")
                    return []

                result = []
                for pos in positions:
                    result.append(Position(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        type="BUY" if pos.type == 0 else "SELL",
                        volume=pos.volume,
                        price_open=pos.price_open,
                        price_current=pos.price_current,
                        stop_loss=pos.sl if pos.sl > 0 else None,
                        take_profit=pos.tp if pos.tp > 0 else None,
                        profit=pos.profit,
                        swap=getattr(pos, "swap", 0.0),
                        commission=getattr(pos, "commission", 0.0),
                        magic_number=pos.magic,
                        comment=pos.comment,
                        time_open=datetime.fromtimestamp(pos.time)
                    ))

                logger.debug(f"Obtenidas {len(result)} posiciones")
                return result

            except Exception as e:
                logger.error(f"Error al obtener posiciones: {str(e)}", exc_info=True)
                return []
    
    def get_market_data(self, symbol: str) -> Optional[MarketData]:
        """
        Obtiene datos de mercado actuales para un símbolo

        Args:
            symbol: Símbolo del instrumento

        Returns:
            MarketData con los datos actuales o None si hay error
        """
        if not self.ensure_connection():
            return None

        with self._mt5_lock:
            try:
                # symbol_info_tick() devuelve None para símbolos que existen en
                # el bróker pero no están seleccionados (visibles) en el Market
                # Watch de la terminal — symbol_select() los activa (no-op si
                # ya lo estaban).
                mt5.symbol_select(symbol, True)
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    logger.error(f"No se pudo obtener tick para {symbol}: {mt5.last_error()}")
                    return None

                info = mt5.symbol_info(symbol)
                daily_change = 0.0
                if info is not None:
                    daily_change = round(getattr(info, 'price_change', 0.0), 2)

                return MarketData(
                    symbol=symbol,
                    bid=tick.bid,
                    ask=tick.ask,
                    last=tick.last,
                    volume=tick.volume,
                    time=datetime.fromtimestamp(tick.time),
                    spread=tick.ask - tick.bid,
                    daily_change=daily_change
                )

            except Exception as e:
                logger.error(f"Error al obtener datos de mercado: {str(e)}", exc_info=True)
                return None
    
    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """
        Obtiene información detallada de un símbolo

        Args:
            symbol: Símbolo del instrumento

        Returns:
            SymbolInfo con la información del símbolo o None si hay error
        """
        if not self.ensure_connection():
            return None

        with self._mt5_lock:
            try:
                mt5.symbol_select(symbol, True)
                info = mt5.symbol_info(symbol)
                if info is None:
                    logger.error(f"No se pudo obtener info para {symbol}: {mt5.last_error()}")
                    return None

                return SymbolInfo(
                    name=info.name,
                    description=info.description,
                    point=info.point,
                    tick_value=getattr(info, 'trade_tick_value', getattr(info, 'tick_value', 0.0)),
                    digits=info.digits,
                    spread=info.spread,
                    trade_contract_size=info.trade_contract_size,
                    volume_min=info.volume_min,
                    volume_max=info.volume_max,
                    volume_step=info.volume_step,
                    trade_mode=info.trade_mode
                )

            except Exception as e:
                logger.error(f"Error al obtener info de símbolo: {str(e)}", exc_info=True)
                return None
    
    def get_available_symbols(self) -> List[str]:
        """
        Obtiene lista de símbolos disponibles

        Returns:
            Lista de nombres de símbolos
        """
        if not self.ensure_connection():
            return []

        with self._mt5_lock:
            try:
                symbols = mt5.symbols_get()
                if symbols is None:
                    logger.error(f"Error al obtener símbolos: {mt5.last_error()}")
                    return []

                return [s.name for s in symbols]

            except Exception as e:
                logger.error(f"Error al procesar símbolos: {str(e)}", exc_info=True)
                return []
    
    def get_historical_data(
        self,
        symbol: str,
        timeframe: int,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        count: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Obtiene datos históricos de velas

        Args:
            symbol: Símbolo del instrumento
            timeframe: Timeframe (usar constantes mt5.TIMEFRAME_*)
            start_date: Fecha de inicio
            end_date: Fecha de fin (opcional)
            count: Número de velas (opcional, alternativa a end_date)

        Returns:
            DataFrame con los datos históricos o None si hay error
        """
        if not self.ensure_connection():
            return None

        with self._mt5_lock:
            try:
                mt5.symbol_select(symbol, True)
                if count:
                    rates = mt5.copy_rates_from(symbol, timeframe, start_date, count)
                elif end_date:
                    rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
                else:
                    logger.error("Debe especificar end_date o count")
                    return None

                if rates is None or len(rates) == 0:
                    logger.error(f"No se obtuvieron datos para {symbol}: {mt5.last_error()}")
                    return None

                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')

                logger.debug(f"Obtenidas {len(df)} velas para {symbol}")
                return df

            except Exception as e:
                logger.error(f"Error al obtener datos históricos: {str(e)}", exc_info=True)
                return None
    

    def get_trade_history(
        self,
        from_date=None,
        to_date=None,
        symbol=None
    ):
        """
        Obtiene historial de deals cerrados de MT5.
        Resuelve la estrategia por magic number — evita el problema de
        que MT5 sobreescriba el comment con "tp" o "sl" al cerrar.
        """
        if not self.ensure_connection():
            return []

        with self._mt5_lock:
            try:
                from datetime import timedelta
                now = datetime.now()
                date_from = from_date or (now - timedelta(days=30))
                date_to   = to_date   or now

                deals = mt5.history_deals_get(date_from, date_to)
                if deals is None:
                    logger.warning(f"No se pudo obtener historial: {mt5.last_error()}")
                    return []

                MAGIC_TO_STRATEGY = {}
                bases = {
                    210000: 'MA_CROSS',  220000: 'RSI',       230000: 'BOLLINGER',
                    240000: 'MACD',      250000: 'BREAKOUT',  260000: 'SUPERTREND',
                    270000: 'EMA_CROSS', 280000: 'WILLIAMS_R', 300000: 'LONDON_ORB',
                }
                for base, name in bases.items():
                    for offset in range(1, 10):
                        MAGIC_TO_STRATEGY[base + offset] = name

                entry_comments = {}
                for d in deals:
                    if d.symbol and d.entry == 0:
                        entry_comments[d.order] = d.comment or ''

                result = []
                for d in deals:
                    if not d.symbol:
                        continue
                    if d.entry != 1:
                        continue
                    if symbol and d.symbol != symbol:
                        continue

                    strategy_name = MAGIC_TO_STRATEGY.get(d.magic)
                    if not strategy_name:
                        orig = entry_comments.get(d.order, d.comment or '')
                        c = orig.upper()
                        if   'MACD'        in c:                      strategy_name = 'MACD'
                        elif 'BOLLINGER'   in c or 'BB MEAN'  in c:  strategy_name = 'BOLLINGER'
                        elif 'SUPERTREND'  in c:                      strategy_name = 'SUPERTREND'
                        elif 'EMA CROSS'   in c or 'EMA_CROSS' in c: strategy_name = 'EMA_CROSS'
                        elif 'WILLIAMS'    in c or 'W%R'       in c: strategy_name = 'WILLIAMS_R'
                        elif 'BREAKOUT'    in c or 'DONCHIAN'  in c: strategy_name = 'BREAKOUT'
                        elif 'LONDON ORB'  in c or 'LONDON_ORB' in c: strategy_name = 'LONDON_ORB'
                        elif 'MA CROSS'    in c or 'MA_CROSS'  in c: strategy_name = 'MA_CROSS'
                        elif 'RSI'         in c:                      strategy_name = 'RSI'
                        else: strategy_name = None

                    if not strategy_name:
                        continue

                    result.append({
                        'ticket':     d.ticket,
                        'order':      d.order,
                        'symbol':     d.symbol,
                        'type':       'BUY' if d.type == 0 else 'SELL',
                        'volume':     d.volume,
                        'price':      d.price,
                        'profit':     d.profit,
                        'commission': getattr(d, 'commission', 0.0),
                        'swap':       getattr(d, 'swap', 0.0),
                        'magic':      d.magic,
                        'strategy':   strategy_name,
                        'comment':    d.comment or '',
                        'time':       datetime.fromtimestamp(d.time).isoformat(),
                    })

                logger.info(
                    f"Historial: {len(result)} deals cerrados "
                    f"({date_from.date()} — {date_to.date()})"
                )
                return result

            except Exception as e:
                logger.error(f"Error al obtener historial: {str(e)}", exc_info=True)
                return []

    def get_closed_trades(self, from_date=None, to_date=None):
        """
        Devuelve round-trips cerrados (entrada+salida) emparejando los deals
        IN (entry==0) y OUT (entry==1) por position_id. Pensado para el backfill
        del modelo ML: necesita la hora y dirección de ENTRADA (no del cierre).

        Cada elemento: {position_id, symbol, magic, direction, entry_time (UTC),
        close_time (UTC), profit (neto incluyendo comisiones y swap)}.
        El profit suma todos los deals de salida (cierre parcial + final).
        """
        if not self.ensure_connection():
            return []

        with self._mt5_lock:
            try:
                from datetime import timedelta
                now = datetime.now()
                date_from = from_date or (now - timedelta(days=90))
                date_to   = to_date   or now

                deals = mt5.history_deals_get(date_from, date_to)
                if not deals:
                    logger.warning(f"get_closed_trades: sin deals ({mt5.last_error()})")
                    return []

                entries = {}   # position_id -> deal de entrada
                exits   = {}   # position_id -> lista de deals de salida
                for d in deals:
                    if not d.symbol:
                        continue
                    if d.entry == 0:        # DEAL_ENTRY_IN
                        entries.setdefault(d.position_id, d)
                    elif d.entry == 1:      # DEAL_ENTRY_OUT
                        exits.setdefault(d.position_id, []).append(d)

                trades = []
                for pid, ein in entries.items():
                    outs = exits.get(pid)
                    if not outs:
                        continue  # posición aún abierta
                    profit = sum(
                        (o.profit or 0.0) + (o.commission or 0.0) + (o.swap or 0.0)
                        for o in outs
                    )
                    close_time = max(o.time for o in outs)
                    trades.append({
                        'position_id': pid,
                        'symbol':      ein.symbol,
                        'magic':       ein.magic,
                        'direction':   'BUY' if ein.type == 0 else 'SELL',
                        'entry_time':  datetime.utcfromtimestamp(ein.time),
                        'close_time':  datetime.utcfromtimestamp(close_time),
                        'profit':      profit,
                    })

                trades.sort(key=lambda t: t['entry_time'])
                logger.info(
                    f"get_closed_trades: {len(trades)} round-trips "
                    f"({date_from.date()} — {date_to.date()})"
                )
                return trades

            except Exception as e:
                logger.error(f"Error en get_closed_trades: {str(e)}", exc_info=True)
                return []


    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
    
    def __del__(self):
        """Destructor para asegurar desconexión"""
        if self._connected:
            self.disconnect()