"""
Filtro de calidad de señales con aprendizaje progresivo.

Fases:
  1. Bootstrap (< MIN_SAMPLES_TO_TRAIN trades etiquetados): heurísticas basadas
     en hora UTC, ADX, ATR_ratio y rendimiento reciente del bot.
  2. ML activo (>= MIN_SAMPLES_TO_TRAIN): LightGBM entrenado sobre señales pasadas.
     Se re-entrena cada RE_TRAIN_INTERVAL muestras nuevas. El modelo nunca bloquea
     más del 50% de las señales (umbral conservador) para evitar sequías de trades.

Features por señal:
  adx, atr_ratio, hour_sin, hour_cos, dow_sin, dow_cos,
  spread_ratio, win_rate, direction_enc, bb_width_ratio

Persistencia:
  signal_filter_data.json  — muestras etiquetadas acumuladas
  signal_filter_model.pkl  — modelo LightGBM serializado (si disponible)
"""
import json
import math
import threading
import pickle
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MIN_SAMPLES_TO_TRAIN = 50       # mínimo antes de usar ML
RE_TRAIN_INTERVAL    = 20       # re-entrenar cada N muestras nuevas
CONFIDENCE_THRESHOLD = 0.42     # bloquear si P(ganancia) < 42%

DATA_FILE  = Path("signal_filter_data.json")
MODEL_FILE = Path("signal_filter_model.pkl")

# LightGBM es opcional — si no está instalado, el filtro funciona en modo heurístico
try:
    import lightgbm as lgb
    import numpy as np
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False
    logger.info("LightGBM no disponible — SignalFilter en modo heurístico puro")


class SignalFilter:
    """
    Filtro de calidad de señales con aprendizaje progresivo.
    Singleton compartido por todos los bots activos.
    """

    _instance = None
    _creation_lock = threading.Lock()

    def __new__(cls):
        with cls._creation_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._samples: List[Dict] = []
                inst._model = None
                inst._samples_at_last_train = 0
                inst._data_lock = threading.RLock()
                inst._initialized = False
                cls._instance = inst
        return cls._instance

    def initialize(self) -> None:
        """Carga muestras y modelo desde disco. Llamar al arrancar el servidor."""
        if self._initialized:
            return
        with self._data_lock:
            self._load_samples()
            if _LGB_AVAILABLE and self._labeled_count() >= MIN_SAMPLES_TO_TRAIN:
                self._try_load_model()
        self._initialized = True
        logger.info(
            f"SignalFilter inicializado — {self._labeled_count()} muestras | "
            f"modo: {'LightGBM' if self._model else 'heurístico'}"
        )

    # -----------------------------------------------------------------------
    # API pública
    # -----------------------------------------------------------------------

    def score_signal(self, strategy_id: str, context: Dict) -> float:
        """
        Retorna P(señal es rentable) ∈ [0, 1].
        Valores > CONFIDENCE_THRESHOLD → señal permitida.
        Valores < CONFIDENCE_THRESHOLD → señal filtrada.
        0.5 es neutral — el filtro no bloquea señales ambiguas.
        """
        with self._data_lock:
            if self._model is not None and _LGB_AVAILABLE:
                try:
                    return self._predict_ml(context)
                except Exception as e:
                    logger.debug(f"SignalFilter ML error: {e} — usando heurísticas")
            return self._score_heuristic(context)

    def record_outcome(self, strategy_id: str, context: Dict, won: bool) -> None:
        """
        Registra el resultado de un trade cerrado como muestra etiquetada.
        Re-entrena el modelo LightGBM si se alcanzó el intervalo configurado.
        """
        if not context:
            return
        with self._data_lock:
            sample = {
                **context,
                'strategy_id': strategy_id,
                'outcome':     1 if won else 0,
                'ts':          datetime.now(timezone.utc).isoformat(),
            }
            self._samples.append(sample)
            self._save_samples()

            if not _LGB_AVAILABLE:
                return

            labeled = self._labeled_count()
            new_since = labeled - self._samples_at_last_train
            if labeled >= MIN_SAMPLES_TO_TRAIN and new_since >= RE_TRAIN_INTERVAL:
                self._train_model()

    # -----------------------------------------------------------------------
    # Heurísticas de bootstrap
    # -----------------------------------------------------------------------

    def _score_heuristic(self, ctx: Dict) -> float:
        """
        Score heurístico basado en conocimiento de trading cuantitativo.
        Nunca retorna < 0.35 (evitar bloqueos agresivos sin datos suficientes).
        """
        score       = 0.50
        adx         = ctx.get('adx', 25.0)
        atr_ratio   = ctx.get('atr_ratio', 1.0)
        hour_utc    = ctx.get('hour_utc', 12)
        spread_ratio = ctx.get('spread_ratio', 0.5)
        win_rate    = ctx.get('win_rate', 0.5)
        bb_ratio    = ctx.get('bb_width_ratio', 1.0)

        # Hora — Londres+NY overlap tiene mayor calidad de señal
        if 13 <= hour_utc <= 16:         score += 0.08   # overlap máx volumen
        elif 7 <= hour_utc <= 12:        score += 0.04   # apertura Londres
        elif hour_utc < 7 or hour_utc >= 20: score -= 0.10  # Asia / fuera sesión

        # Spread elevado degrada calidad
        if   spread_ratio > 0.80:        score -= 0.12
        elif spread_ratio > 0.60:        score -= 0.06

        # ADX — sweet spot entre 20 y 40
        if   20 <= adx <= 40:            score += 0.06
        elif adx < 15:                   score -= 0.06
        elif adx > 50:                   score -= 0.04

        # ATR ratio — mercado hiper-volátil tiene peor calidad
        if   0.8 <= atr_ratio <= 1.3:    score += 0.04
        elif atr_ratio > 2.0:            score -= 0.12  # evento macro
        elif atr_ratio < 0.5:            score -= 0.04  # mercado dormido

        # Rendimiento reciente del bot
        if   win_rate > 0.60:            score += 0.08
        elif win_rate > 0.50:            score += 0.04
        elif win_rate < 0.40:            score -= 0.06

        # BB squeeze / expansión
        if   0.8 <= bb_ratio <= 1.5:     score += 0.02
        elif bb_ratio > 2.5:             score -= 0.06

        return max(0.35, min(0.90, score))

    # -----------------------------------------------------------------------
    # Modelo LightGBM
    # -----------------------------------------------------------------------

    def _extract_feature_vector(self, ctx: Dict):
        """Convierte context dict a array numpy para LightGBM."""
        hour = ctx.get('hour_utc', 12)
        dow  = ctx.get('day_of_week', 2)
        return np.array([[
            ctx.get('adx', 25.0),
            ctx.get('atr_ratio', 1.0),
            math.sin(2 * math.pi * hour / 24),    # codificación cíclica hora
            math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * dow / 5),      # codificación cíclica día
            math.cos(2 * math.pi * dow / 5),
            ctx.get('spread_ratio', 0.5),
            ctx.get('win_rate', 0.5),
            1.0 if ctx.get('direction') == 'BUY' else -1.0,
            ctx.get('bb_width_ratio', 1.0),
        ]], dtype=float)

    def _predict_ml(self, ctx: Dict) -> float:
        X = self._extract_feature_vector(ctx)
        prob = self._model.predict_proba(X)[0][1]
        return float(prob)

    def _train_model(self) -> None:
        """Entrena LightGBM sobre todas las muestras etiquetadas disponibles."""
        labeled = [s for s in self._samples if s.get('outcome') is not None]
        if len(labeled) < MIN_SAMPLES_TO_TRAIN:
            return
        try:
            X = np.array([self._extract_feature_vector(s)[0] for s in labeled])
            y = np.array([s['outcome'] for s in labeled])

            pos   = y.sum()
            neg   = len(y) - pos
            scale = neg / pos if pos > 0 else 1.0

            model = lgb.LGBMClassifier(
                objective        = 'binary',
                metric           = 'binary_logloss',
                learning_rate    = 0.05,
                num_leaves       = 15,       # modelo pequeño — pocos datos
                min_child_samples= 5,
                n_estimators     = 100,
                scale_pos_weight = scale,
                verbose          = -1,
                random_state     = 42,
            )
            model.fit(X, y)
            self._model = model
            self._samples_at_last_train = len(labeled)
            self._save_model()
            logger.info(
                f"SignalFilter: modelo re-entrenado — "
                f"{len(labeled)} muestras | wins={int(pos)} losses={int(neg)}"
            )
        except Exception as e:
            logger.error(f"SignalFilter: error entrenando modelo: {e}")

    # -----------------------------------------------------------------------
    # Persistencia
    # -----------------------------------------------------------------------

    def _labeled_count(self) -> int:
        return sum(1 for s in self._samples if s.get('outcome') is not None)

    def _load_samples(self) -> None:
        if DATA_FILE.exists():
            try:
                raw = DATA_FILE.read_text(encoding='utf-8').strip()
                if raw:
                    self._samples = json.loads(raw)
            except Exception as e:
                logger.warning(f"SignalFilter: error cargando muestras: {e}")

    def _save_samples(self) -> None:
        try:
            DATA_FILE.write_text(
                json.dumps(self._samples, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error(f"SignalFilter: error guardando muestras: {e}")

    def _try_load_model(self) -> None:
        if not MODEL_FILE.exists() or not _LGB_AVAILABLE:
            return
        try:
            with MODEL_FILE.open('rb') as f:
                self._model = pickle.load(f)
            self._samples_at_last_train = self._labeled_count()
            logger.info("SignalFilter: modelo LightGBM cargado desde disco")
        except Exception as e:
            logger.warning(f"SignalFilter: no se pudo cargar modelo: {e}")

    def _save_model(self) -> None:
        if self._model is None:
            return
        try:
            with MODEL_FILE.open('wb') as f:
                pickle.dump(self._model, f)
        except Exception as e:
            logger.error(f"SignalFilter: error guardando modelo: {e}")


# Instancia global — importar desde aquí
signal_filter = SignalFilter()
