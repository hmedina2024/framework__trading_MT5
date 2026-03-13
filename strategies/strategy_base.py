"""
Clase base para estrategias de trading

Mejoras aplicadas vs version anterior:
  1. Cooldown post-trade por simbolo (COOLDOWN_HOURS_AFTER_TRADE)
     - tras cerrar una posicion, bloquea nuevas entradas N horas en ese simbolo
  2. Limite de trades diarios por simbolo (MAX_DAILY_TRADES_PER_SYMBOL)
     - evita over-trading (ej: 10 trades en GBPUSD en 1 hora)
  3. Edad minima de posicion antes de evaluar salida (MIN_POSITION_AGE_SECONDS)
     - evita cerrar en la misma vela/iteracion que se abrio
  4. Registro de tiempo de apertura por ticket (_position_open_times)
  5. cooldowns_active visible en get_statistics() para monitoreo desde frontend
"""
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import pandas as pd

from utils.logger import get_logger
from models.trade_models import TradeRequest, OrderType, Position

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Parametros de proteccion — ajustar segun necesidades
# ---------------------------------------------------------------------------

# Horas sin operar en un simbolo tras cerrar una posicion
COOLDOWN_HOURS_AFTER_TRADE = 2

# Maximo de trades por simbolo en el mismo dia calendario
MAX_DAILY_TRADES_PER_SYMBOL = 3

# Segundos minimos que debe vivir una posicion antes de evaluar el cierre
MIN_POSITION_AGE_SECONDS = 300


class StrategyBase(ABC):
    """Clase base abstracta para estrategias de trading."""

    def __init__(
        self,
        name: str,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols: List[str],
        timeframe: int,
        magic_number: Optional[int] = None
    ):
        """
        Inicializa la estrategia base
        
        Args:
            name: Nombre de la estrategia
            connector: Instancia de PlatformConnector
            order_manager: Instancia de OrderManager
            risk_manager: Instancia de RiskManager
            market_analyzer: Instancia de MarketAnalyzer
            symbols: Lista de símbolos a operar
            timeframe: Timeframe de la estrategia
            magic_number: Número mágico para identificar órdenes
        """
        self.name = name
        self.connector = connector
        self.order_manager = order_manager
        self.risk_manager = risk_manager
        self.market_analyzer = market_analyzer
        self.symbols = symbols
        self.timeframe = timeframe
        self.magic_number = magic_number or 234000
        self.is_running = False
        self._thread = None
        self.positions: Dict[str, Position] = {}
        self._stats = {"trades_count": 0, "wins": 0, "losses": 0}

        # Tiempo de apertura por ticket: {ticket: datetime}
        self._position_open_times: Dict[int, datetime] = {}

        # Cooldown post-trade por simbolo: {symbol: datetime_hasta}
        self._cooldown_until: Dict[str, datetime] = {}

        # Contador de trades diarios: {symbol: {fecha_str: count}}
        self._daily_trades: Dict[str, Dict[str, int]] = {}

        logger.info(f"Estrategia '{name}' inicializada para {symbols}")

    # =======================================================================
    # Metodos abstractos
    # =======================================================================

    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """Analiza el mercado y genera senales de trading."""
        pass

    @abstractmethod
    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        """Calcula precios de entrada, stop loss y take profit."""
        pass

    # =======================================================================
    # Helpers de proteccion
    # =======================================================================

    def _is_in_cooldown(self, symbol: str) -> bool:
        """True si el simbolo esta bloqueado por cooldown post-trade."""
        until = self._cooldown_until.get(symbol)
        if until and datetime.now() < until:
            remaining_min = int((until - datetime.now()).total_seconds() / 60)
            logger.info(
                f"{self.name} | {symbol}: cooldown activo — "
                f"{remaining_min} min restantes (hasta {until.strftime('%H:%M')})"
            )
            return True
        return False

    def _set_cooldown(self, symbol: str):
        """Activa cooldown post-trade para el simbolo."""
        until = datetime.now() + timedelta(hours=COOLDOWN_HOURS_AFTER_TRADE)
        self._cooldown_until[symbol] = until
        logger.info(
            f"{self.name} | {symbol}: cooldown activado "
            f"({COOLDOWN_HOURS_AFTER_TRADE}h) — sin entradas hasta {until.strftime('%H:%M')}"
        )

    def _get_daily_trade_count(self, symbol: str) -> int:
        """Devuelve cuantos trades se han abierto hoy para el simbolo."""
        today = datetime.now().strftime('%Y-%m-%d')
        return self._daily_trades.get(symbol, {}).get(today, 0)

    def _increment_daily_trade_count(self, symbol: str):
        """Incrementa el contador de trades diarios para el simbolo."""
        today = datetime.now().strftime('%Y-%m-%d')
        if symbol not in self._daily_trades:
            self._daily_trades[symbol] = {}
        self._daily_trades[symbol][today] = (
            self._daily_trades[symbol].get(today, 0) + 1
        )

    def _is_daily_limit_reached(self, symbol: str) -> bool:
        """True si ya se alcanzo el maximo de trades diarios para el simbolo."""
        count = self._get_daily_trade_count(symbol)
        if count >= MAX_DAILY_TRADES_PER_SYMBOL:
            logger.info(
                f"{self.name} | {symbol}: limite diario alcanzado "
                f"({count}/{MAX_DAILY_TRADES_PER_SYMBOL} trades hoy) — "
                f"esperando hasta manana"
            )
            return True
        return False

    def _is_position_old_enough(self, ticket: int) -> bool:
        """True si la posicion lleva al menos MIN_POSITION_AGE_SECONDS abierta."""
        open_time = self._position_open_times.get(ticket)
        if open_time is None:
            return True  # sin registro = la consideramos suficientemente vieja
        age_seconds = (datetime.now() - open_time).total_seconds()
        if age_seconds < MIN_POSITION_AGE_SECONDS:
            remaining = int(MIN_POSITION_AGE_SECONDS - age_seconds)
            logger.info(
                f"{self.name} | Ticket {ticket}: posicion muy reciente "
                f"({int(age_seconds)}s abierta, minimo {MIN_POSITION_AGE_SECONDS}s) — "
                f"{remaining}s hasta evaluar salida"
            )
            return False
        return True

    # =======================================================================
    # Ejecucion de senales
    # =======================================================================

    def execute_signal(self, symbol: str, signal: Dict) -> bool:
        """Ejecuta una senal de trading con todas las protecciones activas."""
        try:
            # 1. Posicion ya abierta por este bot
            if self._has_open_position(symbol):
                logger.info(
                    f"Ignorando senal para {symbol}: "
                    "Ya existe una posicion abierta gestionada por este bot."
                )
                return False

            # 2. Cooldown post-trade
            if self._is_in_cooldown(symbol):
                return False

            # 3. Limite de trades diarios
            if self._is_daily_limit_reached(symbol):
                return False

            # 4. Risk manager global
            allowed, reason = self.risk_manager.is_trading_allowed()
            if not allowed:
                logger.warning(f"Trading no permitido: {reason}")
                return False

            # 5. Calcular precios
            prices = self.calculate_entry_exit(symbol, signal)

            # 6. Validar ratio R:R minimo 1:1
            rr_ratio = self.risk_manager.get_risk_reward_ratio(
                prices['entry'],
                prices['stop_loss'],
                prices['take_profit'],
                is_buy=(signal['direction'] == 'BUY')
            )
            MIN_RR_RATIO = 1.0
            if rr_ratio < MIN_RR_RATIO:
                logger.warning(
                    f"Senal rechazada para {symbol}: R:R insuficiente "
                    f"({rr_ratio:.2f} < {MIN_RR_RATIO}). "
                    f"Entry: {prices['entry']}, SL: {prices['stop_loss']}, "
                    f"TP: {prices['take_profit']}"
                )
                return False

            # 7. Calcular volumen
            volume = self.risk_manager.calculate_position_size(
                symbol, prices['entry'], prices['stop_loss']
            )
            if not volume:
                logger.error(f"No se pudo calcular tamanio de posicion para {symbol}")
                return False

            # 8. Crear y validar solicitud
            request = TradeRequest(
                symbol=symbol,
                order_type=OrderType.BUY if signal['direction'] == 'BUY' else OrderType.SELL,
                volume=volume,
                price=prices['entry'],
                stop_loss=prices['stop_loss'],
                take_profit=prices['take_profit'],
                magic_number=self.magic_number,
                comment=f"{self.name} {signal['direction']}"[:31]
            )

            is_valid, msg = self.risk_manager.validate_trade(request)
            if not is_valid:
                logger.warning(f"Operacion rechazada por riesgo: {msg}")
                return False

            # 9. Ejecutar
            result = self.order_manager.open_position(request)

            if result.success:
                logger.info(f"Senal ejecutada exitosamente para {symbol}")
                self.on_trade_opened(symbol, result)
                return True
            else:
                logger.error(f"Error al ejecutar senal: {result.error_message}")
                return False

        except Exception as e:
            logger.error(f"Error al ejecutar senal: {str(e)}", exc_info=True)
            return False

    # =======================================================================
    # Exit conditions — sobreescribir en estrategias hijas
    # =======================================================================

    def check_exit_conditions(self, position: Position) -> bool:
        """Verifica si se deben cerrar posiciones. Base: no cierra."""
        return False

    # =======================================================================
    # Loop principal
    # =======================================================================

    def run_iteration(self) -> None:
        """Ejecuta una iteracion de la estrategia."""
        if not self.connector.is_connected():
            logger.error("No hay conexion con MT5")
            return

        for symbol in self.symbols:
            try:
                df = self.market_analyzer.get_candles(
                    symbol, self.timeframe, count=200
                )
                if df is None or df.empty:
                    logger.warning(f"No se pudieron obtener datos para {symbol}")
                    continue

                signal = self.analyze(symbol, df)
                if signal:
                    logger.info(f"Senal detectada para {symbol}: {signal}")
                    self.execute_signal(symbol, signal)

                self._check_open_positions(symbol)

            except Exception as e:
                logger.error(
                    f"Error en iteracion para {symbol}: {str(e)}", exc_info=True
                )

    def _has_open_position(self, symbol: str) -> bool:
        """Verifica si hay posicion abierta para este bot en el simbolo."""
        positions = self.connector.get_positions(symbol)
        return any(p.magic_number == self.magic_number for p in positions)

    def _check_open_positions(self, symbol: str) -> None:
        """Verifica y gestiona posiciones abiertas."""
        positions = self.connector.get_positions(symbol)

        for position in positions:
            if position.magic_number != self.magic_number:
                continue

            # Guard edad minima — evita cerrar en la misma iteracion que se abrio
            if not self._is_position_old_enough(position.ticket):
                continue

            if self.check_exit_conditions(position):
                logger.info(
                    f"Cerrando posicion {position.ticket} por condiciones de salida"
                )
                result = self.order_manager.close_position(position.ticket)
                if result.success:
                    self.on_trade_closed(position, result)

    # =======================================================================
    # Callbacks apertura / cierre
    # =======================================================================

    def on_trade_opened(self, symbol: str, result) -> None:
        """Callback cuando se abre una operacion."""
        logger.info(f"Trade abierto: {symbol} - Ticket: {result.ticket}")

        # Registrar tiempo de apertura para el guard de edad minima
        self._position_open_times[result.ticket] = datetime.now()

        # Incrementar contador diario
        self._increment_daily_trade_count(symbol)

        self._stats["trades_count"] += 1
        daily_count = self._get_daily_trade_count(symbol)
        logger.info(
            f"Estadisticas de '{self.name}' actualizadas: "
            f"Trades: {self._stats['trades_count']} | "
            f"{symbol} hoy: {daily_count}/{MAX_DAILY_TRADES_PER_SYMBOL}"
        )

    def on_trade_closed(self, position: Position, result) -> None:
        """Callback cuando se cierra una operacion. Activa cooldown."""
        logger.info(
            f"Trade cerrado: {position.symbol} - "
            f"Ticket: {position.ticket} - P&L: {position.profit}"
        )

        # Limpiar registro de tiempo de apertura
        self._position_open_times.pop(position.ticket, None)

        # Activar cooldown para el simbolo
        self._set_cooldown(position.symbol)

        self.update_stats(position.profit)

    def update_stats(self, profit: float) -> None:
        """Actualiza estadisticas tras cerrar una operacion."""
        if profit >= 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1

        logger.info(
            f"Estadisticas de '{self.name}' actualizadas: "
            f"Trades: {self._stats['trades_count']}, "
            f"Wins: {self._stats['wins']}, "
            f"Losses: {self._stats['losses']}"
        )

    # =======================================================================
    # Ciclo de vida del hilo
    # =======================================================================

    def start(self) -> None:
        """Inicia la estrategia en un hilo de ejecucion."""
        if self.is_running:
            logger.warning(f"Estrategia '{self.name}' ya esta en ejecucion.")
            return
        self.is_running = True
        logger.info(f"Estrategia '{self.name}' iniciada")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        """Bucle infinito — llama a run_iteration cada 60 segundos."""
        logger.info(
            f"Hilo de estrategia '{self.name}' ejecutandose en segundo plano..."
        )
        check_interval_seconds = 60

        while self.is_running:
            try:
                self.run_iteration()
            except Exception as e:
                logger.error(
                    f"Excepcion en el hilo de la estrategia '{self.name}': {str(e)}",
                    exc_info=True
                )
            for _ in range(check_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

        logger.info(f"Hilo de estrategia '{self.name}' finalizado.")

    def stop(self) -> None:
        """Detiene la estrategia y su hilo de ejecucion."""
        self.is_running = False
        if self._thread and self._thread.is_alive():
            logger.info(f"Esperando a que el hilo de '{self.name}' termine...")
        logger.info(f"Estrategia '{self.name}' detenida")

    # =======================================================================
    # Estadisticas
    # =======================================================================

    def get_daily_stats(self) -> Dict:
        """Devuelve estadisticas con contadores diarios por simbolo."""
        stats = self._stats.copy()
        stats["win_rate"] = (
            (stats["wins"] / stats["trades_count"]) * 100
            if stats["trades_count"] > 0 else 0.0
        )
        today = datetime.now().strftime('%Y-%m-%d')
        stats["daily_trades"] = {
            sym: data.get(today, 0)
            for sym, data in self._daily_trades.items()
        }
        return stats

    def get_statistics(self) -> Dict:
        """Estadisticas completas incluyendo cooldowns activos."""
        daily_stats = self.get_daily_stats()
        account_info = self.connector.get_account_info()

        return {
            'strategy_name': self.name,
            'is_running': self.is_running,
            'symbols': self.symbols,
            'daily_stats': daily_stats,
            'account_balance': account_info.balance if account_info else 0,
            'account_equity': account_info.equity if account_info else 0,
            'open_positions': len(self.connector.get_positions()),
            'cooldowns_active': {
                sym: until.strftime('%H:%M')
                for sym, until in self._cooldown_until.items()
                if datetime.now() < until
            }
        }