"""
Gestor de riesgo para operaciones de trading
"""
from typing import Optional, Dict
import threading
import time
from datetime import datetime

from utils.logger import get_logger
from models.trade_models import TradeRequest, AccountInfo
from config.settings import settings

logger = get_logger(__name__)

class RiskManager:
    def __init__(self, connector, max_daily_loss=None, max_open_positions=None):
        self.connector = connector
        self.max_risk_per_trade = settings.MAX_RISK_PER_TRADE
        self.max_daily_loss = max_daily_loss or settings.MAX_DAILY_LOSS
        # DEMO: maximo de posiciones aumentado a 30 para evaluar multiples estrategias
        self.max_open_positions = max_open_positions or 30
        account_info = self.connector.get_account_info()
        self.balance_at_start = account_info.balance if account_info else None
        self._daily_loss_alerted = False
        # Cooldown por simbolo: cuando MT5 cierra por SL/TP el bot no lo detecta
        # de inmediato. Este dict bloquea re-entradas por SYMBOL_REENTRY_COOLDOWN segundos
        # después de que desaparece una posición que existía antes.
        self._symbol_last_closed: dict = {}   # {symbol: datetime_de_cierre}
        SYMBOL_REENTRY_COOLDOWN_SECS = 120    # 2 min de espera tras cierre por SL/TP
        self._reentry_cooldown = SYMBOL_REENTRY_COOLDOWN_SECS
        logger.info(
            f"RiskManager inicializado | "
            f"Riesgo por trade: {self.max_risk_per_trade*100}% | "
            f"Posiciones max: {self.max_open_positions} | "
            f"Limite por activo: 1 posicion global | "
            f"Balance inicial: ${self.balance_at_start:.2f}"
        )
        self._start_daily_reset_scheduler()

    def validate_trade(self, request: TradeRequest) -> tuple[bool, str]:
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"
        allowed, reason = self.is_trading_allowed()
        if not allowed:
            return False, reason
        # Limite global: 1 posicion por activo entre todos los bots
        if not self._check_max_positions_per_symbol(request.symbol):
            return False, f"Ya existe una posicion abierta en {request.symbol} (limite global por activo)"

        if not self._check_margin_available(account_info, request):
            return False, "Margen insuficiente para la operación"
        logger.info(f"✅ Validación de riesgo aprobada para {request.symbol}")
        return True, "Operación aprobada"

    def _check_max_positions(self) -> bool:
        try:
            positions = self.connector.get_positions()
            current_positions = len(positions) if positions else 0
            if current_positions >= self.max_open_positions:
                logger.warning(f"⚠️ Máximo de posiciones alcanzado: {current_positions}/{self.max_open_positions}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error al verificar posiciones abiertas: {e}")
            return False

    def _check_max_positions_per_symbol(self, symbol: str) -> bool:
        """
        Verifica que no haya posicion abierta en el simbolo (limite global).
        Tambien aplica un cooldown de 2 minutos tras un cierre por SL/TP de MT5:
        cuando MT5 cierra una posicion directamente el bot no lo detecta hasta
        el siguiente ciclo de 60s — sin este cooldown volvería a abrir inmediatamente.
        """
        try:
            positions = self.connector.get_positions(symbol)
            now = datetime.now()

            if positions and len(positions) > 0:
                # Hay posicion abierta — bloquear y registrar que existe
                logger.warning(
                    f"Posicion global bloqueada para {symbol}: "
                    f"ya hay {len(positions)} posicion(es) abiertas "
                    f"(independiente del bot que las abrio)"
                )
                return False

            # No hay posicion abierta — verificar cooldown post-cierre
            last_closed = self._symbol_last_closed.get(symbol)
            if last_closed:
                elapsed = (now - last_closed).total_seconds()
                if elapsed < self._reentry_cooldown:
                    remaining = int(self._reentry_cooldown - elapsed)
                    logger.info(
                        f"{symbol}: cooldown post-SL/TP activo — "
                        f"re-entrada bloqueada {remaining}s mas "
                        f"(evita re-entrada inmediata tras cierre por MT5)"
                    )
                    return False
                else:
                    # Cooldown expirado — limpiar
                    del self._symbol_last_closed[symbol]

            # Registrar que no hay posicion ahora (posible cierre reciente por SL/TP)
            # Solo registrar si habia posicion en la llamada anterior
            # Esto lo hace el bot via notify_position_closed()
            return True

        except Exception as e:
            logger.error(f"Error al verificar posiciones por simbolo: {e}")
            return True

    def notify_position_closed(self, symbol: str) -> None:
        """
        Llamar cuando un bot detecta que una posicion fue cerrada (por SL, TP o manual).
        Activa el cooldown de re-entrada para el simbolo durante _reentry_cooldown segundos.
        Esto previene que el bot vuelva a abrir inmediatamente tras un cierre por MT5.
        """
        self._symbol_last_closed[symbol] = datetime.now()
        logger.info(
            f"{symbol}: cooldown post-cierre activado "
            f"({self._reentry_cooldown}s) — re-entrada bloqueada"
        )

    def _check_margin_available(self, account_info: AccountInfo, request: TradeRequest) -> bool:
        symbol_info = self.connector.get_symbol_info(request.symbol)
        if not symbol_info:
            return False
        price = request.price
        if not price:
            market_data = self.connector.get_market_data(request.symbol)
            if not market_data:
                return False
            order_type_str = str(request.order_type.value) if hasattr(request.order_type, 'value') else str(request.order_type)
            price = market_data.ask if "BUY" in order_type_str.upper() else market_data.bid
        if account_info.leverage <= 0:
            return False
        required_margin = (request.volume * symbol_info.trade_contract_size * price) / account_info.leverage
        if required_margin > account_info.margin_free:
            logger.warning(f"⚠️ Margen insuficiente. Requerido: {required_margin:.2f}, Disponible: {account_info.margin_free:.2f}")
            return False
        return True

    def _check_daily_loss(self, account_info: AccountInfo) -> tuple[bool, str]:
        # DEMO MODE: limite de perdida diaria desactivado para evaluacion de estrategias.
        # Cambiar a False para reactivar en produccion.
        DEMO_MODE = True
        if DEMO_MODE:
            if self.balance_at_start and self.balance_at_start > 0:
                current_balance = account_info.balance
                daily_loss = self.balance_at_start - current_balance
                daily_loss_pct = daily_loss / self.balance_at_start
                logger.debug(f"[DEMO] Perdida diaria actual: {daily_loss_pct*100:.2f}% — sin bloqueo")
            return True, ""
        # --- Logica de produccion (activa cuando DEMO_MODE = False) ---
        if not self.balance_at_start or self.balance_at_start <= 0:
            return True, ""
        current_balance = account_info.balance
        daily_loss = self.balance_at_start - current_balance
        daily_loss_pct = daily_loss / self.balance_at_start
        if daily_loss_pct >= self.max_daily_loss:
            if not self._daily_loss_alerted:
                logger.warning(
                    f"LIMITE DE PERDIDA DIARIA ALCANZADO: "
                    f"Perdida: ${daily_loss:.2f} ({daily_loss_pct*100:.2f}%) | "
                    f"Limite: {self.max_daily_loss*100:.0f}% | "
                    f"Balance inicial: ${self.balance_at_start:.2f} | "
                    f"Actual: ${current_balance:.2f} | "
                    f"Bots bloqueados hasta medianoche."
                )
                self._daily_loss_alerted = True
            return False, f"Limite de perdida diaria alcanzado: {daily_loss_pct*100:.2f}% (max {self.max_daily_loss*100:.0f}%)"
        if daily_loss_pct > self.max_daily_loss * 0.7:
            remaining = (self.max_daily_loss - daily_loss_pct) * self.balance_at_start
            logger.warning(f"Perdida diaria al {daily_loss_pct*100:.2f}% — Quedan ${remaining:.2f} antes del bloqueo")
        self._daily_loss_alerted = False
        return True, ""

    def reset_daily_stats(self):
        account_info = self.connector.get_account_info()
        if account_info:
            self.balance_at_start = account_info.balance
            self._daily_loss_alerted = False
            logger.info(f"📅 Stats diarias reseteadas. Nuevo balance base: ${self.balance_at_start:.2f}")

    def _start_daily_reset_scheduler(self):
        def scheduler_loop():
            while True:
                try:
                    now = datetime.now()
                    seconds_until_midnight = (23 - now.hour) * 3600 + (59 - now.minute) * 60 + (60 - now.second)
                    logger.info(f"⏰ Próximo reset diario en {seconds_until_midnight // 3600}h {(seconds_until_midnight % 3600) // 60}m")
                    time.sleep(seconds_until_midnight)
                    self.reset_daily_stats()
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Error en scheduler de reset diario: {e}")
                    time.sleep(60)
        thread = threading.Thread(target=scheduler_loop, daemon=True)
        thread.start()
        logger.info("⏰ Scheduler de reset diario iniciado")

    def calculate_position_size(self, symbol: str, entry_price: float, stop_loss_price: float, risk_percentage=None):
        account_info = self.connector.get_account_info()
        if not account_info:
            logger.error("No se pudo obtener info de cuenta para calcular tamaño de posición")
            return None
        symbol_info = self.connector.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"No se pudo obtener info de {symbol} para calcular tamaño de posición")
            return None
        risk_pct = risk_percentage or self.max_risk_per_trade
        risk_money = account_info.equity * risk_pct
        risk_points = abs(entry_price - stop_loss_price)
        if risk_points <= 0 or symbol_info.point <= 0 or symbol_info.tick_value <= 0:
            logger.error(f"Valores inválidos. Risk points: {risk_points}, point: {symbol_info.point}, tick_value: {symbol_info.tick_value}")
            return None
        risk_per_lot = (risk_points / symbol_info.point) * symbol_info.tick_value
        if risk_per_lot <= 0:
            logger.error(f"El riesgo por lote es cero o negativo ({risk_per_lot}).")
            return None
        volume = risk_money / risk_per_lot
        volume = symbol_info.normalize_volume(volume)
        if volume < symbol_info.volume_min:
            volume = symbol_info.volume_min
            logger.warning(f"Volumen ajustado al mínimo permitido: {volume} lotes")
        market_data = self.connector.get_market_data(symbol)
        if market_data and account_info.leverage > 0:
            price = market_data.ask
            required_margin = (volume * symbol_info.trade_contract_size * price) / account_info.leverage
            if required_margin > account_info.margin_free:
                min_margin = (symbol_info.volume_min * symbol_info.trade_contract_size * price) / account_info.leverage
                if min_margin > account_info.margin_free:
                    logger.warning(f"Margen insuficiente incluso para volumen mínimo. Requerido: {min_margin:.2f}, Disponible: {account_info.margin_free:.2f}")
                    return None
                volume = symbol_info.volume_min
                logger.warning(f"Volumen reducido al mínimo por margen insuficiente: {volume} lotes")
        logger.info(f"Tamaño de posición calculado para {symbol}: {volume:.2f} lotes (Riesgo: {risk_pct*100}%)")
        return volume

    def get_risk_reward_ratio(self, entry_price: float, stop_loss: float, take_profit: float, is_buy: bool = True) -> float:
        if is_buy:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        if risk <= 0:
            return 0.0
        return reward / risk

    def is_trading_allowed(self) -> tuple[bool, str]:
        account_info = self.connector.get_account_info()
        if not account_info:
            return False, "No se pudo obtener información de cuenta"
        if account_info.is_margin_call:
            return False, "Cuenta en margin call"
        if account_info.margin > 0 and account_info.margin_level < 200:
            return False, f"Nivel de margen bajo: {account_info.margin_level:.2f}%"
        daily_ok, daily_msg = self._check_daily_loss(account_info)
        if not daily_ok:
            return False, daily_msg
        if not self._check_max_positions():
            return False, f"Número máximo de posiciones alcanzado ({self.max_open_positions})"
        return True, "Trading permitido"