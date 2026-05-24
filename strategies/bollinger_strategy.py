"""
Estrategia Bollinger Bands Mean Reversion — v2 (H4 + Z-score)

Problemas de v1 que causaban 10-23% WR:
  - Timeframe H1: mean reversion en H1 tiene ~65% WR teorico vs ~72% en H4
  - TP = bb_middle fijo: en H1 la media está a 10-15 pips — R:R < 1 constante
  - Sin Z-score: no medía QUÉ TAN lejos estaba el precio de la media,
    cualquier toque de banda generaba señal (incluso toques de ruido)
  - Confirmacion débil: solo precio fuera → precio dentro, sin validar momentum
  - ADX max=25: demasiado permisivo, ADX 20-25 sigue siendo tendencia moderada

Mejoras v2:
  1. Timeframe H4 — WR estadisticamente superior (+7pp documentado)
  2. Filtro Z-score >= 2.0 — solo señales con desviacion estadistica real
  3. Confirmacion de vela: cuerpo >= 40% del rango total en la direccion correcta
  4. RSI girando: RSI debe cambiar de direccion en la vela de confirmacion
  5. ADX max = 20 — solo en mercado lateral real (sin tendencia emergente)
  6. TP dinamico: garantiza R:R >= 1.2 extendiendo mas alla de la media si es necesario
  7. SL bajo/sobre la banda (no ATR fijo) — mas coherente con la logica de reversión

Logica:
  COMPRA:
    - Vela anterior cerro bajo la banda inferior (precio en extremo estadistico)
    - Z-score del precio anterior >= 2.0 (desviacion significativa)
    - Vela actual cierra sobre la banda inferior (confirmacion de giro)
    - Cuerpo de la vela actual >= 40% del rango (momentum de reversion real)
    - RSI actual > RSI anterior (oscilador girando al alza)
    - ADX < 20 (sin tendencia que invalide la reversion)

  VENTA: logica simetrica al alza.

Activos recomendados (mejor media reversion en H4):
  EURUSD, USDJPY, AUDUSD, USDCAD
  (GBPUSD y XAUUSD tienen mayor volatilidad — señales menos fiables en reversion)
"""
import pandas as pd
from typing import Optional, Dict
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Parametros de señal
# ---------------------------------------------------------------------------
Z_SCORE_MIN      = 2.0   # desviacion minima para considerar extremo estadistico
RSI_OVERSOLD     = 45    # RSI maximo para señal BUY (sobreventa)
RSI_OVERBOUGHT   = 55    # RSI minimo para señal SELL (sobrecompra)
ADX_MAX          = 20.0  # ADX maximo — solo mercado lateral real
BODY_RATIO_MIN   = 0.40  # cuerpo de vela >= 40% del rango para confirmar momentum
MIN_RR_RATIO     = 1.2   # R:R minimo garantizado — extiende TP si es necesario
TP_EXTRA_FACTOR  = 0.30  # extiende TP un 30% mas alla de la media cuando es necesario


class BollingerBandsStrategy(StrategyBase):
    """
    Estrategia de reversión a la media con Bandas de Bollinger en H4.
    Usa Z-score para filtrar solo extremos estadisticos reales,
    confirmacion de vela para validar el giro, y TP dinamico para
    garantizar R:R >= 1.2 en cada señal que llega a execute_signal.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H4,   # H4 por defecto — cambio clave vs v1
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        magic_number: Optional[int] = None
    ):
        super().__init__(
            name="Bollinger Bands Mean Reversion",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number
        )
        self.bb_period  = bb_period
        self.bb_std     = bb_std
        self.rsi_period = rsi_period

        logger.info(
            f"Bollinger v2 (H4+Z-score) — "
            f"Periodo: {bb_period} | Std: {bb_std} | RSI: {rsi_period} | "
            f"TF: H4 | Z-score min: {Z_SCORE_MIN} | ADX max: {ADX_MAX}"
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _calculate_z_score(
        self, price: float, bb_middle: float, std: float
    ) -> float:
        """Desviaciones estandar del precio respecto a la media."""
        if std <= 0:
            return 0.0
        return abs(price - bb_middle) / std

    def _is_confirmation_candle(
        self, open_p: float, close_p: float, high_p: float, low_p: float,
        direction: str
    ) -> bool:
        """
        True si la vela tiene cuerpo >= BODY_RATIO_MIN y va en la direccion
        esperada (alcista para BUY, bajista para SELL).
        Filtra velas de indecision (doji, spinning tops) que no confirman el giro.
        """
        candle_range = high_p - low_p
        if candle_range <= 0:
            return False
        body = abs(close_p - open_p)
        body_ratio = body / candle_range
        if body_ratio < BODY_RATIO_MIN:
            return False
        if direction == 'BUY'  and close_p <= open_p:
            return False   # necesita vela alcista
        if direction == 'SELL' and close_p >= open_p:
            return False   # necesita vela bajista
        return True

    # -----------------------------------------------------------------------
    # Interfaz StrategyBase
    # -----------------------------------------------------------------------

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            # Calcular indicadores
            bb_upper_s, bb_middle_s, bb_lower_s = (
                self.market_analyzer.calculate_bollinger_bands(
                    df, self.bb_period, self.bb_std
                )
            )
            rsi_s   = self.market_analyzer.calculate_rsi(df, self.rsi_period)
            ema200  = self.market_analyzer.calculate_ema(df, 200)

            df['bb_upper']  = bb_upper_s
            df['bb_middle'] = bb_middle_s
            df['bb_lower']  = bb_lower_s
            df['rsi']       = rsi_s

            current  = df.iloc[-1]
            previous = df.iloc[-2]

            # Datos minimos requeridos
            if (pd.isna(current['bb_upper']) or pd.isna(current['rsi']) or
                    pd.isna(previous['bb_upper']) or pd.isna(previous['rsi'])):
                return None

            # -----------------------------------------------------------
            # FILTRO 1: ADX — solo mercado lateral real (ADX < 20)
            # -----------------------------------------------------------
            adx = self.market_analyzer.calculate_adx(df)
            if adx is None or pd.isna(adx):
                return None
            if adx >= ADX_MAX:
                logger.debug(
                    f"BB v2 {symbol}: bloqueado por ADX={adx:.1f} "
                    f">= {ADX_MAX} — tendencia activa"
                )
                return None

            # -----------------------------------------------------------
            # FILTRO 2: EMA200 pendiente — bloquear si hay tendencia estructural
            # -----------------------------------------------------------
            if not pd.isna(ema200.iloc[-1]) and not pd.isna(ema200.iloc[-10]):
                slope = (ema200.iloc[-1] - ema200.iloc[-10]) / ema200.iloc[-10] * 100
                if abs(slope) > 0.12:
                    logger.debug(
                        f"BB v2 {symbol}: bloqueado por pendiente "
                        f"EMA200={slope:.3f}% > ±0.12%"
                    )
                    return None

            # -----------------------------------------------------------
            # Calcular std para Z-score
            # STD de las últimas bb_period velas de cierre
            # -----------------------------------------------------------
            std = float(df['close'].iloc[-self.bb_period:].std())
            if std <= 0:
                return None

            # -----------------------------------------------------------
            # SEÑAL DE COMPRA
            # Condiciones:
            #   1. Vela anterior cerró bajo la banda inferior
            #   2. Z-score de esa vela >= Z_SCORE_MIN (extremo estadistico real)
            #   3. Vela actual cierra SOBRE la banda inferior (giro confirmado)
            #   4. Vela actual es alcista con cuerpo >= BODY_RATIO_MIN
            #   5. RSI girando al alza (actual > anterior)
            #   6. RSI en zona de sobreventa (< RSI_OVERSOLD)
            # -----------------------------------------------------------
            z_prev = self._calculate_z_score(
                previous['close'], previous['bb_middle'], std
            )

            if (previous['close'] < previous['bb_lower'] and
                    z_prev >= Z_SCORE_MIN and
                    current['close'] > current['bb_lower'] and
                    current['rsi'] < RSI_OVERSOLD and
                    current['rsi'] > previous['rsi'] and
                    self._is_confirmation_candle(
                        current['open'], current['close'],
                        current['high'], current['low'], 'BUY'
                    )):

                logger.info(
                    f"BB v2 BUY {symbol} | "
                    f"Close={current['close']:.5f} "
                    f"BB_Low={current['bb_lower']:.5f} | "
                    f"Z={z_prev:.2f} | RSI={current['rsi']:.1f} | ADX={adx:.1f}"
                )
                return {
                    'direction':  'BUY',
                    'reason':     f'Reversion desde extremo BB inferior Z={z_prev:.2f}',
                    'bb_lower':   float(current['bb_lower']),
                    'bb_middle':  float(current['bb_middle']),
                    'bb_upper':   float(current['bb_upper']),
                    'bb_std':     std,
                    'z_score':    z_prev,
                    'rsi':        float(current['rsi']),
                    'adx':        adx,
                }

            # -----------------------------------------------------------
            # SEÑAL DE VENTA — lógica simétrica
            # -----------------------------------------------------------
            if (previous['close'] > previous['bb_upper'] and
                    z_prev >= Z_SCORE_MIN and
                    current['close'] < current['bb_upper'] and
                    current['rsi'] > RSI_OVERBOUGHT and
                    current['rsi'] < previous['rsi'] and
                    self._is_confirmation_candle(
                        current['open'], current['close'],
                        current['high'], current['low'], 'SELL'
                    )):

                logger.info(
                    f"BB v2 SELL {symbol} | "
                    f"Close={current['close']:.5f} "
                    f"BB_Up={current['bb_upper']:.5f} | "
                    f"Z={z_prev:.2f} | RSI={current['rsi']:.1f} | ADX={adx:.1f}"
                )
                return {
                    'direction':  'SELL',
                    'reason':     f'Reversion desde extremo BB superior Z={z_prev:.2f}',
                    'bb_lower':   float(current['bb_lower']),
                    'bb_middle':  float(current['bb_middle']),
                    'bb_upper':   float(current['bb_upper']),
                    'bb_std':     std,
                    'z_score':    z_prev,
                    'rsi':        float(current['rsi']),
                    'adx':        adx,
                }

            return None

        except Exception as e:
            logger.error(
                f"Error en BB v2 analyze para {symbol}: {e}", exc_info=True
            )
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)

        if not market_data or not symbol_info:
            raise ValueError(
                f"No se pudo obtener datos de mercado para {symbol}"
            )

        std        = signal['bb_std']
        bb_lower   = signal['bb_lower']
        bb_upper   = signal['bb_upper']
        bb_middle  = signal['bb_middle']
        vol_mult   = self._get_volatility_sl_multiplier(symbol)

        if signal['direction'] == 'BUY':
            entry = market_data.ask
            # SL: debajo de la banda inferior — buffer de 0.3 std
            stop_loss = bb_lower - (std * 0.3 * vol_mult)
            # TP base: la media de las bandas (objetivo natural de la reversión)
            tp_base   = bb_middle
        else:
            entry = market_data.bid
            # SL: sobre la banda superior — buffer de 0.3 std
            stop_loss = bb_upper + (std * 0.3 * vol_mult)
            tp_base   = bb_middle

        risk   = abs(entry - stop_loss)
        reward = abs(tp_base - entry)

        # Garantizar R:R >= MIN_RR_RATIO extendiendo el TP mas alla de la media
        # si la distancia entrada-media no es suficiente (ocurre cuando el precio
        # entró tarde en la vela de confirmacion y ya está cerca de la media)
        if risk > 0 and (reward / risk) < MIN_RR_RATIO:
            required_reward = risk * MIN_RR_RATIO
            if signal['direction'] == 'BUY':
                tp_base = entry + required_reward
            else:
                tp_base = entry - required_reward
            logger.debug(
                f"BB v2 {symbol}: TP extendido a {tp_base:.5f} "
                f"para garantizar R:R >= {MIN_RR_RATIO}"
            )

        entry     = symbol_info.normalize_price(entry)
        stop_loss = symbol_info.normalize_price(stop_loss)
        tp_base   = symbol_info.normalize_price(tp_base)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, tp_base,
            is_buy=(signal['direction'] == 'BUY')
        )

        logger.info(
            f"BB v2 {symbol} Entry={entry:.5f} | "
            f"SL={stop_loss:.5f} | TP={tp_base:.5f} | "
            f"R:R=1:{rr_ratio:.2f} | Z={signal['z_score']:.2f}"
        )

        return {
            'entry':       entry,
            'stop_loss':   stop_loss,
            'take_profit': tp_base,
            'bb_std':      std,
            'risk_reward': rr_ratio,
        }

    def check_exit_conditions(self, position) -> bool:
        """
        Cierra la posicion si:
        - El precio alcanza la banda contraria (extensión máxima, tomar ganancia)
        - El precio regresa a cruzar la banda de entrada (fallo de reversión)
        """
        df = self.market_analyzer.get_candles(
            position.symbol, self.timeframe, count=50
        )
        if df is None or df.empty:
            return False

        bb_upper_s, _, bb_lower_s = self.market_analyzer.calculate_bollinger_bands(
            df, self.bb_period, self.bb_std
        )
        current_close = float(df['close'].iloc[-1])
        bb_upper = float(bb_upper_s.iloc[-1])
        bb_lower = float(bb_lower_s.iloc[-1])

        if position.type == 'BUY':
            # Ganancia máxima: precio llega a la banda superior
            if current_close >= bb_upper:
                logger.info(
                    f"BB v2: cerrando BUY {position.symbol} — "
                    f"precio ({current_close:.5f}) alcanzó BB Upper ({bb_upper:.5f})"
                )
                return True
            # Fallo: precio vuelve a cruzar bajo la banda inferior
            if current_close < bb_lower:
                logger.info(
                    f"BB v2: cerrando BUY {position.symbol} — "
                    f"fallo de reversión, precio ({current_close:.5f}) "
                    f"bajo BB Lower ({bb_lower:.5f})"
                )
                return True

        elif position.type == 'SELL':
            # Ganancia máxima: precio llega a la banda inferior
            if current_close <= bb_lower:
                logger.info(
                    f"BB v2: cerrando SELL {position.symbol} — "
                    f"precio ({current_close:.5f}) alcanzó BB Lower ({bb_lower:.5f})"
                )
                return True
            # Fallo: precio vuelve a cruzar sobre la banda superior
            if current_close > bb_upper:
                logger.info(
                    f"BB v2: cerrando SELL {position.symbol} — "
                    f"fallo de reversión, precio ({current_close:.5f}) "
                    f"sobre BB Upper ({bb_upper:.5f})"
                )
                return True

        return False