"""
Gestor de riesgo para operaciones de trading
"""
from typing import Optional, Dict
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from utils.logger import get_logger
from models.trade_models import TradeRequest, AccountInfo
from config.settings import settings

BALANCE_STATE_FILE = Path("balance_state.json")

# Tope de margen por operación: ninguna posición individual puede comprometer
# más de este % del equity en margen. Evita que un SL muy ajustado dispare un
# lotaje desproporcionado (ej: 0.91 lotes en una cuenta de $1000 por un SL de
# 2 pips). Es un cap de seguridad independiente del riesgo % por trade.
MAX_MARGIN_PER_TRADE_PCT = 0.25

# Tolerancia al forzar el volumen al mínimo del bróker: si el SL calculado
# por ATR es angosto en relación al lote mínimo tradeable (típico en XAUUSD
# con cuentas chicas), el riesgo real puede superar por mucho el % objetivo.
# Si el riesgo forzado excede este múltiplo del objetivo, se rechaza el
# trade en vez de tomarlo silenciosamente sobre-arriesgado.
MAX_MIN_LOT_RISK_MULTIPLIER = 1.5

# ---------------------------------------------------------------------------
# Modo demo: False en producción para que el límite de pérdida diaria aplique.
# Cambia a True únicamente para pruebas sin restricciones de drawdown diario.
# ---------------------------------------------------------------------------
DEMO_MODE = False

logger = get_logger(__name__)

class RiskManager:
    def __init__(self, connector, max_daily_loss=None, max_open_positions=None):
        self.connector = connector
        self.max_risk_per_trade = settings.MAX_RISK_PER_TRADE
        self.max_daily_loss = max_daily_loss or settings.MAX_DAILY_LOSS
        # DEMO: maximo de posiciones aumentado a 30 para evaluar multiples estrategias
        self.max_open_positions = max_open_positions or 30
        self._daily_loss_alerted = False
        self._portfolio_drawdown_alerted = False
        self._symbol_last_closed: dict = {}
        self._reentry_cooldown = 120  # segundos de cooldown post SL/TP

        # Cargar balance_at_start desde disco para sobrevivir reinicios intradía.
        # Si el archivo existe y tiene la fecha de hoy, usamos ese balance como base;
        # de lo contrario obtenemos el balance actual y lo persistimos.
        saved = self._load_balance_at_start()
        if saved is not None:
            self.balance_at_start = saved
            logger.info(f"RiskManager: balance_at_start recuperado desde disco: ${saved:.2f}")
        else:
            account_info = self.connector.get_account_info()
            self.balance_at_start = account_info.balance if account_info else None
            if self.balance_at_start:
                self._save_balance_at_start(self.balance_at_start)

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
        Aplica cooldown de 2 min tras cierre por SL/TP para evitar re-entradas
        inmediatas antes de que el bot detecte el cierre.
        """
        try:
            positions = self.connector.get_positions(symbol)
            now = datetime.now()

            if positions and len(positions) > 0:
                logger.warning(
                    f"Posicion global bloqueada para {symbol}: "
                    f"ya hay {len(positions)} posicion(es) abiertas"
                )
                return False

            # Cooldown post-cierre
            last_closed = self._symbol_last_closed.get(symbol)
            if last_closed:
                elapsed = (now - last_closed).total_seconds()
                if elapsed < self._reentry_cooldown:
                    remaining = int(self._reentry_cooldown - elapsed)
                    logger.info(
                        f"{symbol}: cooldown post-SL/TP — "
                        f"re-entrada bloqueada {remaining}s"
                    )
                    return False
                else:
                    del self._symbol_last_closed[symbol]
            return True
        except Exception as e:
            logger.error(f"Error al verificar posiciones por simbolo: {e}")
            return True

    def notify_position_closed(self, symbol: str) -> None:
        """Activa cooldown de re-entrada cuando se cierra una posicion."""
        self._symbol_last_closed[symbol] = datetime.now()
        logger.info(f"{symbol}: cooldown post-cierre activado ({self._reentry_cooldown}s)")

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
            self._save_balance_at_start(self.balance_at_start)
            self._daily_loss_alerted = False
            self._portfolio_drawdown_alerted = False
            logger.info(f"📅 Stats diarias reseteadas. Nuevo balance base: ${self.balance_at_start:.2f}")

    def _load_balance_at_start(self) -> Optional[float]:
        """Carga el balance_at_start del día desde disco. Retorna None si no existe o es de otro día."""
        try:
            if BALANCE_STATE_FILE.exists():
                data = json.loads(BALANCE_STATE_FILE.read_text(encoding="utf-8"))
                if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                    return float(data["balance"])
        except Exception as e:
            logger.warning(f"No se pudo cargar balance_state.json: {e}")
        return None

    def _save_balance_at_start(self, balance: float) -> None:
        """Persiste el balance_at_start con la fecha actual para sobrevivir reinicios."""
        try:
            BALANCE_STATE_FILE.write_text(
                json.dumps({"date": datetime.now().strftime("%Y-%m-%d"), "balance": balance}),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Error guardando balance_state.json: {e}")

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
        if volume <= symbol_info.volume_min:
            forced_risk = symbol_info.volume_min * risk_per_lot
            if forced_risk > risk_money * MAX_MIN_LOT_RISK_MULTIPLIER:
                logger.warning(
                    f"{symbol}: SL demasiado angosto para el lote mínimo — "
                    f"riesgo forzado ${forced_risk:.2f} excede {MAX_MIN_LOT_RISK_MULTIPLIER}x "
                    f"el objetivo (${risk_money:.2f}, {risk_pct*100:.1f}%). Señal rechazada."
                )
                return None
            volume = symbol_info.volume_min
            if forced_risk > risk_money:
                logger.warning(
                    f"{symbol}: volumen ajustado al mínimo permitido ({volume} lotes) — "
                    f"riesgo real ${forced_risk:.2f} ({forced_risk/account_info.equity*100:.2f}%) "
                    f"supera el objetivo de {risk_pct*100:.1f}%"
                )
        market_data = self.connector.get_market_data(symbol)

        # Cap de seguridad por margen: limitar el lotaje para que una sola posición
        # no comprometa más del MAX_MARGIN_PER_TRADE_PCT del equity. Protege contra
        # el lotaje explosivo cuando el SL es muy ajustado.
        if market_data and account_info.leverage > 0:
            price = market_data.ask
            margin_per_lot = (symbol_info.trade_contract_size * price) / account_info.leverage
            if margin_per_lot > 0:
                max_volume = (account_info.equity * MAX_MARGIN_PER_TRADE_PCT) / margin_per_lot
                capped = max(symbol_info.volume_min, symbol_info.normalize_volume(max_volume))
                if capped < volume:
                    logger.warning(
                        f"{symbol}: volumen limitado por cap de margen "
                        f"{volume:.2f} → {capped:.2f} lotes "
                        f"(máx {MAX_MARGIN_PER_TRADE_PCT*100:.0f}% del equity por trade)"
                    )
                    volume = capped

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

    def _check_portfolio_drawdown(self, account_info: AccountInfo) -> tuple[bool, str]:
        """
        Bloquea todas las entradas si el drawdown flotante del portfolio supera
        MAX_PORTFOLIO_DRAWDOWN. Protege contra pérdidas coordinadas en eventos macro
        cuando varios bots pierden simultáneamente en mercados correlacionados.
        Drawdown = (balance - equity) / balance — mide pérdidas flotantes abiertas.
        """
        if account_info.balance <= 0:
            return True, ""
        drawdown = (account_info.balance - account_info.equity) / account_info.balance
        if drawdown > settings.MAX_PORTFOLIO_DRAWDOWN:
            msg = (
                f"Drawdown de portfolio {drawdown*100:.1f}% supera el límite "
                f"({settings.MAX_PORTFOLIO_DRAWDOWN*100:.0f}%) — "
                f"todas las entradas bloqueadas hasta reducir exposición"
            )
            if not self._portfolio_drawdown_alerted:
                logger.warning(f"⛔ PORTFOLIO DRAWDOWN: {msg}")
                self._portfolio_drawdown_alerted = True
                try:
                    from utils.telegram_notifier import send_message
                    send_message(
                        f"⛔ <b>Portfolio Drawdown — Entradas BLOQUEADAS</b>\n"
                        f"Drawdown flotante: <b>{drawdown*100:.1f}%</b> "
                        f"(límite {settings.MAX_PORTFOLIO_DRAWDOWN*100:.0f}%)\n"
                        f"Balance: <b>${account_info.balance:.2f}</b> | "
                        f"Equity: <b>${account_info.equity:.2f}</b>\n"
                        f"Pérdida flotante: <b>-${account_info.balance - account_info.equity:.2f}</b>\n"
                        f"Nuevas entradas bloqueadas hasta reducir exposición.",
                        silent=False
                    )
                except Exception:
                    pass
            return False, msg
        # Si el drawdown baja del límite, resetear la alerta para poder disparar de nuevo
        if self._portfolio_drawdown_alerted:
            self._portfolio_drawdown_alerted = False
        # Aviso preventivo al 70% del límite
        if drawdown > settings.MAX_PORTFOLIO_DRAWDOWN * 0.70:
            warn_remaining = (settings.MAX_PORTFOLIO_DRAWDOWN - drawdown) * account_info.balance
            logger.warning(
                f"Drawdown de portfolio en {drawdown*100:.1f}% — "
                f"${warn_remaining:.2f} de margen antes del bloqueo total"
            )
        return True, ""

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
        portfolio_ok, portfolio_msg = self._check_portfolio_drawdown(account_info)
        if not portfolio_ok:
            return False, portfolio_msg
        if not self._check_max_positions():
            return False, f"Número máximo de posiciones alcanzado ({self.max_open_positions})"
        return True, "Trading permitido"