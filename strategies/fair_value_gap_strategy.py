"""
Fair Value Gap (FVG) — Estrategia de Desequilibrio de Precio

Un FVG es el espacio entre el high de la vela N-2 y el low de la vela N
(alcista) o entre el low de la vela N-2 y el high de la vela N (bajista),
causado por una vela N-1 de impulso fuerte. El precio tiende a regresar
a rellenar ese desequilibrio antes de continuar la tendencia.

Lógica de entrada:
  1. Detectar FVGs frescos (no mitigados) en las últimas LOOKBACK_CANDLES velas.
  2. Esperar que el precio regrese a tocar la zona del FVG.
  3. Confirmar que la tendencia H4 (EMA50) apoya la dirección.
  4. Entrar en la dirección del FVG original.

Parámetros clave:
  LOOKBACK_CANDLES   — cuántas velas atrás buscar FVGs (default 30)
  MIN_FVG_ATR_RATIO  — tamaño mínimo del FVG como fracción de ATR (default 0.3)
  MAX_FVG_AGE_CANDLES— FVGs más viejos que esto se descartan (default 20)
  TP_ATR_MULT        — take profit en múltiplos de ATR (default 2.5)
  SL_ATR_MULT        — stop loss en múltiplos de ATR (default 1.5)

Activos recomendados: XAUUSD, GBPUSD, EURUSD, BTCUSD
Timeframe: H1 (con filtro de tendencia H4)
"""
import pandas as pd
from typing import Optional, Dict, List
import MetaTrader5 as mt5

from strategies.strategy_base import StrategyBase
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Parámetros
# ---------------------------------------------------------------------------
LOOKBACK_CANDLES    = 30    # velas atrás donde buscar FVGs
MIN_FVG_ATR_RATIO   = 0.3   # FVG debe ser >= 30% de 1 ATR para ser válido
MAX_FVG_AGE_CANDLES = 20    # descartar FVGs más viejos que esto
TP_ATR_MULT         = 2.5   # TP a 2.5x ATR desde la entrada
SL_ATR_MULT         = 1.5   # SL a 1.5x ATR desde el FVG extremo
ADX_MIN             = 20.0  # mercado con mínima direccionalidad


class FairValueGapStrategy(StrategyBase):
    """
    Opera retornos de precio a zonas de desequilibrio (Fair Value Gaps)
    en dirección de la tendencia H4.
    """

    def __init__(
        self,
        connector,
        order_manager,
        risk_manager,
        market_analyzer,
        symbols,
        timeframe=mt5.TIMEFRAME_H1,
        magic_number=None,
    ):
        super().__init__(
            name="Fair Value Gap",
            connector=connector,
            order_manager=order_manager,
            risk_manager=risk_manager,
            market_analyzer=market_analyzer,
            symbols=symbols,
            timeframe=timeframe,
            magic_number=magic_number,
        )
        logger.info(
            f"FVG Strategy — lookback={LOOKBACK_CANDLES} velas, "
            f"min_size={MIN_FVG_ATR_RATIO}x ATR, TP={TP_ATR_MULT}x ATR"
        )

    # -----------------------------------------------------------------------
    # Detección de FVGs
    # -----------------------------------------------------------------------

    def _find_fvgs(self, df: pd.DataFrame, atr: float) -> List[Dict]:
        """
        Escanea las últimas LOOKBACK_CANDLES velas buscando FVGs frescos.

        FVG alcista: high[i-2] < low[i]  (hueco entre vela i-2 y vela i)
        FVG bajista: low[i-2]  > high[i]

        Retorna lista de dicts ordenados del más reciente al más viejo.
        Solo incluye FVGs no mitigados (precio actual no los ha penetrado).
        """
        min_size = atr * MIN_FVG_ATR_RATIO
        current_price = df['close'].iloc[-1]
        fvgs = []

        start = max(2, len(df) - LOOKBACK_CANDLES)
        for i in range(start, len(df) - 1):  # -1: última vela puede estar incompleta
            age = (len(df) - 1) - i
            if age > MAX_FVG_AGE_CANDLES:
                continue

            h1 = df['high'].iloc[i - 2]
            l1 = df['low'].iloc[i - 2]
            h3 = df['high'].iloc[i]
            l3 = df['low'].iloc[i]

            # FVG alcista: la vela impulso dejó un hueco por encima de la vela anterior
            if l3 > h1:
                size = l3 - h1
                if size >= min_size:
                    # Mitigado si el precio ya bajó al interior del gap
                    mitigated = current_price < h1 + size * 0.5
                    if not mitigated:
                        fvgs.append({
                            'direction': 'BUY',
                            'top':       l3,     # límite superior del gap
                            'bottom':    h1,     # límite inferior del gap
                            'mid':       (l3 + h1) / 2,
                            'size':      size,
                            'age':       age,
                            'idx':       i,
                        })

            # FVG bajista: la vela impulso dejó un hueco por debajo de la vela anterior
            elif h3 < l1:
                size = l1 - h3
                if size >= min_size:
                    mitigated = current_price > h3 + size * 0.5
                    if not mitigated:
                        fvgs.append({
                            'direction': 'SELL',
                            'top':       l1,     # límite superior del gap
                            'bottom':    h3,     # límite inferior del gap
                            'mid':       (l1 + h3) / 2,
                            'size':      size,
                            'age':       age,
                            'idx':       i,
                        })

        # Ordenar: más recientes y más grandes primero
        fvgs.sort(key=lambda x: (x['age'], -x['size']))
        return fvgs

    def _price_in_fvg(self, price: float, fvg: Dict) -> bool:
        """True si el precio actual está tocando o dentro del FVG."""
        return fvg['bottom'] <= price <= fvg['top']

    # -----------------------------------------------------------------------
    # Interfaz StrategyBase
    # -----------------------------------------------------------------------

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        try:
            if len(df) < LOOKBACK_CANDLES + 5:
                return None

            atr_series = self.market_analyzer.calculate_atr(df, period=14)
            if atr_series is None or atr_series.isna().all():
                return None
            atr = float(atr_series.iloc[-1])
            if atr <= 0:
                return None

            adx = self.market_analyzer.calculate_adx(df, period=14)
            if adx is None or adx < ADX_MIN:
                return None

            current_close = float(df['close'].iloc[-1])
            current_low   = float(df['low'].iloc[-1])
            current_high  = float(df['high'].iloc[-1])

            fvgs = self._find_fvgs(df, atr)
            if not fvgs:
                return None

            # Tendencia H4 para filtrar dirección
            try:
                df_h4 = self.market_analyzer.get_candles(symbol, mt5.TIMEFRAME_H4, count=60)
                h4_trend = None
                if df_h4 is not None and len(df_h4) >= 55:
                    ema50 = self.market_analyzer.calculate_ema(df_h4, 50)
                    slope = (ema50.iloc[-1] - ema50.iloc[-10]) / ema50.iloc[-10] * 100
                    if slope > 0.03:
                        h4_trend = 'UP'
                    elif slope < -0.03:
                        h4_trend = 'DOWN'
            except Exception:
                h4_trend = None

            for fvg in fvgs:
                direction = fvg['direction']

                # Filtro de tendencia H4: solo operar FVG en dirección del trend
                if h4_trend == 'UP'   and direction == 'SELL':
                    continue
                if h4_trend == 'DOWN' and direction == 'BUY':
                    continue

                # Verificar que el precio actual está dentro o tocando el FVG
                if direction == 'BUY':
                    touching = self._price_in_fvg(current_close, fvg) or \
                               self._price_in_fvg(current_low,   fvg)
                else:
                    touching = self._price_in_fvg(current_close, fvg) or \
                               self._price_in_fvg(current_high,  fvg)

                if not touching:
                    continue

                # Confirmar momentum de continuación con la vela actual
                prev_close = float(df['close'].iloc[-2])
                if direction == 'BUY'  and current_close < prev_close:
                    continue  # vela bajista dentro del FVG alcista — esperar confirmación
                if direction == 'SELL' and current_close > prev_close:
                    continue

                logger.debug(
                    f"FVG {direction} en {symbol} | "
                    f"Gap [{fvg['bottom']:.5f} - {fvg['top']:.5f}] "
                    f"size={fvg['size']:.5f} age={fvg['age']}v | "
                    f"ADX={adx:.1f} H4={h4_trend}"
                )
                return {
                    'direction': direction,
                    'reason':    f"FVG {direction} — gap [{fvg['bottom']:.5f}-{fvg['top']:.5f}]",
                    'fvg_top':    fvg['top'],
                    'fvg_bottom': fvg['bottom'],
                    'fvg_size':   fvg['size'],
                    'atr':        atr,
                    'adx':        adx,
                }

            return None

        except Exception as e:
            logger.error(f"FVG analyze error {symbol}: {e}", exc_info=True)
            return None

    def calculate_entry_exit(self, symbol: str, signal: Dict) -> Dict:
        market_data = self.connector.get_market_data(symbol)
        symbol_info = self.connector.get_symbol_info(symbol)
        if not market_data or not symbol_info:
            raise ValueError(f"No se pudo obtener datos de mercado para {symbol}")

        atr        = signal['atr']
        fvg_top    = signal['fvg_top']
        fvg_bottom = signal['fvg_bottom']
        adx        = signal.get('adx', 25.0)
        vol_mult   = self._get_volatility_sl_multiplier(symbol)
        tp_mult    = self._get_regime_tp_multiplier(adx)

        if signal['direction'] == 'BUY':
            entry       = market_data.ask
            # SL por debajo del FVG (invalida la zona de demanda)
            stop_loss   = symbol_info.normalize_price(fvg_bottom - atr * 0.5 * vol_mult)
            take_profit = symbol_info.normalize_price(entry + atr * TP_ATR_MULT * tp_mult)
        else:
            entry       = market_data.bid
            # SL por encima del FVG (invalida la zona de oferta)
            stop_loss   = symbol_info.normalize_price(fvg_top + atr * 0.5 * vol_mult)
            take_profit = symbol_info.normalize_price(entry - atr * TP_ATR_MULT * tp_mult)

        entry       = symbol_info.normalize_price(entry)

        rr_ratio = self.risk_manager.get_risk_reward_ratio(
            entry, stop_loss, take_profit,
            is_buy=(signal['direction'] == 'BUY')
        )
        logger.info(
            f"FVG Entry: {entry} | SL: {stop_loss} | TP: {take_profit} | R:R=1:{rr_ratio:.2f}"
        )
        return {
            'entry':       entry,
            'stop_loss':   stop_loss,
            'take_profit': take_profit,
            'atr':         atr,
            'risk_reward': rr_ratio,
        }

    def check_exit_conditions(self, position) -> bool:
        """
        Cierra anticipadamente si el precio cruza al otro lado del FVG
        (la zona de desequilibrio fue absorbida sin continuación).
        La lógica principal de cierre es el SL/TP de MT5.
        """
        df = self.market_analyzer.get_candles(position.symbol, self.timeframe, count=20)
        if df is None or df.empty:
            return False

        atr_series = self.market_analyzer.calculate_atr(df, period=14)
        if atr_series is None or atr_series.isna().all():
            return False

        current_price = float(df['close'].iloc[-1])

        # Si la posición está perdiendo más de 1.5 ATR sin llegar al SL
        # y el momentum ya revirtió, salir anticipadamente
        atr = float(atr_series.iloc[-1])
        if position.type == 'BUY':
            if current_price < position.price_open - atr * SL_ATR_MULT:
                logger.info(f"FVG | {position.symbol}: salida anticipada BUY — momentum revertido")
                return True
        else:
            if current_price > position.price_open + atr * SL_ATR_MULT:
                logger.info(f"FVG | {position.symbol}: salida anticipada SELL — momentum revertido")
                return True

        return False
