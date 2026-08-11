"""
Servicio central de Trading
Encapsula la lógica de negocio y mantiene el estado del framework MT5
"""
from platform_connector import PlatformConnector
from core import OrderManager, RiskManager, MarketAnalyzer
from strategies import (
    MovingAverageCrossStrategy,
    RSIStrategy,
    BollingerBandsStrategy,
    MACDStrategy,
    BreakoutStrategy,
    StrategyBase,
    LondonORBStrategy,
)
from strategies.supertrend_strategy import SupertrendStrategy
from strategies.ema_crossover_strategy import EMACrossoverStrategy
from strategies.williams_r_strategy import WilliamsRStrategy
from strategies.fair_value_gap_strategy import FairValueGapStrategy
from strategies.ny_open_orb_strategy import NYOpenORBStrategy
from models import TradeRequest, TradeResult, OrderType
from utils import get_logger
from core.regime_detector import RegimeDetector
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
import MetaTrader5 as mt5

logger = get_logger(__name__)

# Archivo donde se persiste la lista de bots activos
# Se actualiza cada vez que se inicia o detiene un bot
BOTS_CONFIG_FILE = Path("bots_config.json")


class TradingService:
    """
    Singleton service que gestiona la conexión MT5 y todos los componentes del framework
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TradingService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.connector = None
        self.order_manager = None
        self.risk_manager = None
        self.market_analyzer = None
        self.active_strategies: Dict[str, StrategyBase] = {}
        self.regime_detector: Optional[RegimeDetector] = None
        self._initialized = True
        
    async def initialize(self) -> bool:
        """Inicializa la conexión con MT5 y los gestores"""
        try:
            # Ejecutar conexión síncrona en un thread aparte para no bloquear
            loop = asyncio.get_event_loop()
            connected = await loop.run_in_executor(None, self._connect_sync)
            
            if connected:
                self.order_manager = OrderManager(self.connector)
                self.risk_manager = RiskManager(self.connector)
                self.market_analyzer = MarketAnalyzer(self.connector)
                # Inicializar detector de régimen de mercado
                self.regime_detector = RegimeDetector(
                    market_analyzer=self.market_analyzer,
                    trading_service=self
                )
                logger.info("TradingService inicializado correctamente")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error inicializando TradingService: {e}")
            return False
            
    def _connect_sync(self) -> bool:
        """Conexión síncrona a MT5"""
        self.connector = PlatformConnector(auto_connect=True)
        return self.connector.is_connected()
        
    async def shutdown(self):
        """Cierra conexiones y detiene estrategias"""
        # Detener scheduler de régimen
        if self.regime_detector:
            self.regime_detector.stop_scheduler()

        # Detener todas las estrategias
        for name, strategy in self.active_strategies.items():
            strategy.stop()
            logger.info(f"Estrategia {name} detenida")
            
        if self.connector:
            self.connector.disconnect()
            logger.info("TradingService desconectado")
            
    def is_connected(self) -> bool:
        # ensure_connection() (no is_connected() a secas) para que se auto-repare.
        # is_connected() del connector marca _connected=False permanentemente ante
        # cualquier fallo transitorio de mt5.account_info() (esperable con 16+ bots
        # golpeando la API de MT5 concurrentemente) y nunca vuelve a intentarlo.
        # Los hilos de las estrategias no sufren esto porque llaman ensure_connection()
        # en cada iteración; los endpoints de la API llamaban is_connected() directo
        # y quedaban bloqueados para siempre aunque el trading siguiera funcionando.
        return self.connector is not None and self.connector.ensure_connection()
        
    def get_account_info(self):
        if not self.is_connected():
            return None
        return self.connector.get_account_info()
        
    def get_open_positions(self):
        if not self.is_connected():
            return []
        return self.connector.get_positions()
        
    def get_open_positions_count(self) -> int:
        positions = self.get_open_positions()
        return len(positions) if positions else 0
        
    def get_active_strategies_count(self) -> int:
        return len(self.active_strategies)
        
    # --- Market Data ---
    
    def get_market_data(self, symbol: str):
        if not self.is_connected():
            return None
        return self.connector.get_market_data(symbol)
        
    def get_market_analysis(self, symbol: str, timeframe: int = mt5.TIMEFRAME_H1):
        if not self.is_connected() or not self.market_analyzer:
            return None
        return self.market_analyzer.get_market_analysis(symbol, timeframe)
        
    # --- Strategies ---
    
    # Catálogo de estrategias disponibles con sus metadatos
    STRATEGY_CATALOG = {
        "MA_CROSS": {
            "name": "MA Cross + RSI",
            "description": "Cruce de medias móviles EMA 12/26 confirmado por RSI. Clásico y confiable.",
            "timeframe": "H1",
            "class": "MovingAverageCrossStrategy",
        },
        "RSI": {
            "name": "RSI Oversold/Overbought",
            "description": "Opera rebotes desde zonas de sobreventa (<30) y sobrecompra (>70) del RSI.",
            "timeframe": "H1",
            "class": "RSIStrategy",
        },
        "BOLLINGER": {
            "name": "Bollinger Bands Mean Reversion",
            "description": "Reversión a la media en H4 con filtro Z-score >= 2.0. Solo opera en mercados laterales reales (ADX < 20). Confirmación de vela y RSI girando.",
            "timeframe": "H4",
            "class": "BollingerBandsStrategy",
        },
        "MACD": {
            "name": "MACD Histogram Momentum",
            "description": "Sigue el momentum cuando el histograma MACD cambia de signo con filtro EMA 200.",
            "timeframe": "H1",
            "class": "MACDStrategy",
        },
        "BREAKOUT": {
            "name": "Donchian Breakout",
            "description": "Ruptura del canal de máximos/mínimos de 20 velas. Inspirado en Turtle Trading.",
            "timeframe": "H4",
            "class": "BreakoutStrategy",
        },
        "SUPERTREND": {
            "name": "Supertrend",
            "description": "Senales de tendencia basadas en ATR. Ideal para XAUUSD y USDJPY. Pocas senales pero de alta calidad.",
            "timeframe": "H1",
            "class": "SupertrendStrategy",
        },
        "EMA_CROSS": {
            "name": "EMA Crossover",
            "description": "Cruce dorado/muerte de EMA 9/21 con filtro EMA 200 y pendiente. La estrategia mas usada en trading algoritmico profesional.",
            "timeframe": "H1",
            "class": "EMACrossoverStrategy",
        },
        "WILLIAMS_R": {
            "name": "Williams %R",
            "description": "Reversiones desde zonas extremas de Williams %R con filtro EMA 50. Complementa a Bollinger para mayor cobertura.",
            "timeframe": "H1",
            "class": "WilliamsRStrategy",
        },
        "LONDON_ORB": {
            "name": "London Opening Range Breakout",
            "description": "Breakout del rango de las 3 primeras velas H1 del London Open (07-10 UTC). Alta efectividad en GBPUSD y XAUUSD. Una entrada por sesión.",
            "timeframe": "H1",
            "class": "LondonORBStrategy",
        },
        "FVG": {
            "name": "Fair Value Gap",
            "description": "Opera retornos a zonas de desequilibrio institucional (FVG/imbalances). El precio tiende a regresar a llenar el gap antes de continuar. Ideal para XAUUSD y GBPUSD.",
            "timeframe": "H1",
            "class": "FairValueGapStrategy",
        },
        "NY_ORB": {
            "name": "NY Open ORB",
            "description": "Breakout del rango de las 2 primeras velas H1 del NY Open (13-15 UTC). Opera el overlap Londres+NY, el período de mayor volumen del día. Ideal para US30, XAUUSD y GBPUSD.",
            "timeframe": "H1",
            "class": "NYOpenORBStrategy",
        },
    }

    def _save_bots_config(self) -> None:
        """
        Guarda la lista de bots activos en bots_config.json.
        Se llama cada vez que se inicia o detiene un bot.
        Al reiniciar el servidor, _load_bots_config relanza todos los bots del archivo.
        """
        try:
            config = []
            for strategy_id, strategy in self.active_strategies.items():
                # strategy_id tiene formato "TIPO_SIMBOLO"
                # El tipo puede tener _ propio: EMA_CROSS, WILLIAMS_R, MA_CROSS
                # Solución: iterar el catálogo y ver cuál es prefijo del strategy_id
                strategy_type = None
                symbol = None
                for catalog_type in self.STRATEGY_CATALOG.keys():
                    prefix = f"{catalog_type}_"
                    if strategy_id.startswith(prefix):
                        strategy_type = catalog_type
                        symbol = strategy_id[len(prefix):]
                        break
                if strategy_type and symbol:
                    config.append({
                        'strategy_type': strategy_type,
                        'symbol': symbol
                    })

            BOTS_CONFIG_FILE.write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            logger.info(f"Configuracion de bots guardada: {len(config)} bots en {BOTS_CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Error guardando configuracion de bots: {e}")

    def _load_bots_config(self) -> int:
        """
        Carga y relanza los bots desde bots_config.json al iniciar el servidor.
        Retorna el numero de bots relanzados exitosamente.
        Si el archivo no existe o está vacío, no hace nada.
        """
        if not BOTS_CONFIG_FILE.exists():
            logger.info("No hay configuracion de bots guardada — servidor inicia sin bots activos")
            return 0

        try:
            raw = BOTS_CONFIG_FILE.read_text(encoding='utf-8').strip()
            if not raw:
                return 0

            config = json.loads(raw)
            if not config:
                return 0

            logger.info(f"Cargando {len(config)} bots desde {BOTS_CONFIG_FILE}...")
            launched = 0
            for entry in config:
                strategy_type = entry.get('strategy_type')
                symbol        = entry.get('symbol')
                if not strategy_type or not symbol:
                    continue
                success = self.start_strategy(symbol, strategy_type)
                if success:
                    launched += 1
                    logger.info(f"  Auto-arrancado: {strategy_type} en {symbol}")
                else:
                    logger.warning(f"  No se pudo auto-arrancar: {strategy_type} en {symbol}")

            logger.info(f"Auto-arranque completado: {launched}/{len(config)} bots activos")
            return launched

        except json.JSONDecodeError as e:
            logger.error(f"bots_config.json corrupto: {e} — ignorando auto-arranque")
            return 0
        except Exception as e:
            logger.error(f"Error cargando configuracion de bots: {e}")
            return 0

    def get_strategy_catalog(self) -> list:
        """Retorna el catálogo de estrategias disponibles"""
        return [
            {"id": k, **v}
            for k, v in self.STRATEGY_CATALOG.items()
        ]

    def start_strategy(self, symbol: str, strategy_type: str = "MA_CROSS") -> bool:
        """Inicia una nueva instancia de estrategia"""
        if not self.is_connected():
            return False

        # Validación de entrada: rechaza tipos de estrategia desconocidos y
        # símbolos que no existen en MT5. Sin esto, un valor mal escrito (ej.
        # el placeholder literal "{symbol}" desde Swagger) creaba un bot que
        # fallaba cada iteración con "Terminal: Call failed" y quedaba guardado
        # en bots_config.json, ocupando un slot y ensuciando los logs.
        if strategy_type not in self.STRATEGY_CATALOG:
            logger.error(f"Tipo de estrategia inválido: '{strategy_type}'")
            return False

        symbol = (symbol or "").strip().upper()
        if not symbol or self.connector.get_symbol_info(symbol) is None:
            logger.error(
                f"Símbolo inválido o no disponible en MT5: '{symbol}' — "
                f"no se inicia el bot {strategy_type}_{symbol}"
            )
            return False

        # Veto de combinaciones tóxicas (SYMBOL_STRATEGY_BLOCKLIST en
        # regime_detector). Antes solo lo respetaba el ciclo periódico del
        # RegimeDetector (cada 4h) — el auto-arranque al iniciar el servidor
        # llamaba aquí directamente y podía revivir un bot recién vetado si
        # seguía en bots_config.json. Chequearlo en este único punto de
        # entrada cubre todos los caminos: auto-arranque, API manual y
        # RegimeDetector por igual.
        from core.regime_detector import SYMBOL_STRATEGY_BLOCKLIST
        if (strategy_type, symbol) in SYMBOL_STRATEGY_BLOCKLIST:
            logger.warning(
                f"No se inicia {strategy_type}_{symbol}: combinación vetada "
                f"(perdedora estructural en backtest)"
            )
            return False

        strategy_id = f"{strategy_type}_{symbol}"

        if strategy_id in self.active_strategies:
            logger.warning(f"Estrategia {strategy_id} ya está activa")
            return False

        try:
            # Magic number fijo y determinista: base por estrategia + offset por simbolo
            # Esto permite identificar la estrategia desde el historial de MT5
            # aunque el comment haya sido sobreescrito por "tp" o "sl"
            STRATEGY_MAGIC_BASE = {
                'MA_CROSS':   210000,
                'RSI':        220000,
                'BOLLINGER':  230000,
                'MACD':       240000,
                'BREAKOUT':   250000,
                'SUPERTREND': 260000,
                'EMA_CROSS':  270000,
                'WILLIAMS_R': 280000,
                'LONDON_ORB': 300000,
                'FVG':        310000,
                'NY_ORB':     320000,
            }
            SYMBOL_OFFSET = {
                'EURUSD': 1, 'GBPUSD': 2, 'USDJPY': 3, 'XAUUSD': 4,
                'AUDUSD': 5, 'USDCAD': 6, 'US30':   7, 'BTCUSD': 8,
            }
            base   = STRATEGY_MAGIC_BASE.get(strategy_type, 290000)
            offset = SYMBOL_OFFSET.get(symbol, 9)
            unique_magic_number = base + offset
            logger.info(f"Magic Number fijo {unique_magic_number} asignado a {strategy_id} ({strategy_type}+{symbol})")

            common_args = dict(
                connector=self.connector,
                order_manager=self.order_manager,
                risk_manager=self.risk_manager,
                market_analyzer=self.market_analyzer,
                symbols=[symbol],
                magic_number=unique_magic_number
            )

            if strategy_type == "MA_CROSS":
                strategy = MovingAverageCrossStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "RSI":
                strategy = RSIStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "BOLLINGER":
                strategy = BollingerBandsStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H4
                )
            elif strategy_type == "MACD":
                strategy = MACDStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "BREAKOUT":
                strategy = BreakoutStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H4
                )
            elif strategy_type == "SUPERTREND":
                strategy = SupertrendStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "EMA_CROSS":
                strategy = EMACrossoverStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "WILLIAMS_R":
                strategy = WilliamsRStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "LONDON_ORB":
                strategy = LondonORBStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "FVG":
                strategy = FairValueGapStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            elif strategy_type == "NY_ORB":
                strategy = NYOpenORBStrategy(
                    **common_args, timeframe=mt5.TIMEFRAME_H1
                )
            else:
                logger.error(f"Tipo de estrategia desconocida: {strategy_type}")
                return False

            # Asignar el ID único ANTES de start(): start() llama _load_stats(),
            # que usa _strategy_id para leer/escribir stats_{TIPO}_{SIMBOLO}.json.
            # Sin esto las stats se guardaban por NOMBRE (stats_EMA_CROSSOVER.json),
            # mezclando todos los símbolos y dejando CIEGO al filtro de rendimiento
            # del RegimeDetector (que lee stats_{TIPO}_{SIMBOLO}.json).
            strategy._strategy_id = strategy_id
            strategy.start()
            self.active_strategies[strategy_id] = strategy
            logger.info(f"Estrategia {strategy_id} iniciada")
            self._save_bots_config()
            return True

        except Exception as e:
            logger.error(f"Error al iniciar estrategia {strategy_id}: {e}")
            return False
            
    def run_backtest(
        self,
        symbol: str,
        strategy_type: str,
        days: int = 90,
        initial_balance: float = 1000.0,
        risk_pct: float = 0.01
    ) -> dict:
        """
        Ejecuta un backtest simple sobre datos históricos de MT5.
        Descarga velas H1 del período indicado, simula las señales
        de la estrategia vela a vela y calcula métricas de rendimiento.
        No abre posiciones reales — es completamente simulado.
        """
        if not self.is_connected():
            return {'error': 'MT5 no conectado'}

        try:
            import MetaTrader5 as mt5
            from datetime import datetime, timedelta

            # Mapear timeframe
            TF_MAP = {
                'MA_CROSS':   mt5.TIMEFRAME_H1, 'RSI':        mt5.TIMEFRAME_H1,
                'BOLLINGER':  mt5.TIMEFRAME_H4, 'MACD':       mt5.TIMEFRAME_H1,
                'BREAKOUT':   mt5.TIMEFRAME_H4, 'SUPERTREND': mt5.TIMEFRAME_H1,
                'EMA_CROSS':  mt5.TIMEFRAME_H1, 'WILLIAMS_R': mt5.TIMEFRAME_H1,
                'LONDON_ORB': mt5.TIMEFRAME_H1, 'FVG':        mt5.TIMEFRAME_H1,
                'NY_ORB':     mt5.TIMEFRAME_H1,
            }
            timeframe = TF_MAP.get(strategy_type, mt5.TIMEFRAME_H1)
            candles_needed = days * 24 if timeframe == mt5.TIMEFRAME_H1 else days * 6

            # Obtener datos históricos
            df_full = self.market_analyzer.get_candles(symbol, timeframe, count=candles_needed + 250)
            if df_full is None or df_full.empty:
                return {'error': f'No hay datos históricos para {symbol}'}

            # Instanciar estrategia en modo simulación (sin magic, sin órdenes reales)
            common_args = dict(
                connector=self.connector,
                order_manager=self.order_manager,
                risk_manager=self.risk_manager,
                market_analyzer=self.market_analyzer,
                symbols=[symbol],
                magic_number=999999
            )
            strategy_map = {
                'MA_CROSS':   ('MovingAverageCrossStrategy', mt5.TIMEFRAME_H1),
                'RSI':        ('RSIStrategy', mt5.TIMEFRAME_H1),
                'BOLLINGER':  ('BollingerBandsStrategy', mt5.TIMEFRAME_H4),
                'MACD':       ('MACDStrategy', mt5.TIMEFRAME_H1),
                'BREAKOUT':   ('BreakoutStrategy', mt5.TIMEFRAME_H4),
                'SUPERTREND': ('SupertrendStrategy', mt5.TIMEFRAME_H1),
                'EMA_CROSS':  ('EMACrossoverStrategy', mt5.TIMEFRAME_H1),
                'WILLIAMS_R': ('WilliamsRStrategy', mt5.TIMEFRAME_H1),
                'LONDON_ORB': ('LondonORBStrategy', mt5.TIMEFRAME_H1),
                'FVG':        ('FairValueGapStrategy', mt5.TIMEFRAME_H1),
                'NY_ORB':     ('NYOpenORBStrategy', mt5.TIMEFRAME_H1),
            }
            class_name, tf = strategy_map.get(strategy_type, ('MACDStrategy', mt5.TIMEFRAME_H1))
            strategy_classes = {
                'MovingAverageCrossStrategy': MovingAverageCrossStrategy,
                'RSIStrategy': RSIStrategy,
                'BollingerBandsStrategy': BollingerBandsStrategy,
                'MACDStrategy': MACDStrategy,
                'BreakoutStrategy': BreakoutStrategy,
                'SupertrendStrategy': SupertrendStrategy,
                'EMACrossoverStrategy': EMACrossoverStrategy,
                'WilliamsRStrategy': WilliamsRStrategy,
                'LondonORBStrategy': LondonORBStrategy,
                'FairValueGapStrategy': FairValueGapStrategy,
                'NYOpenORBStrategy': NYOpenORBStrategy,
            }
            StratClass = strategy_classes.get(class_name, MACDStrategy)
            strategy = StratClass(**common_args, timeframe=tf)

            # Simular vela a vela (walk-forward)
            balance      = initial_balance
            peak_balance = initial_balance
            max_drawdown = 0.0
            trades       = []
            equity_curve = [{'balance': round(balance, 2), 'idx': 0}]

            # Warm-up: necesitamos al menos 210 velas para indicadores (EMA200 + buffer)
            warmup = 210
            total  = len(df_full)

            # Índice de vela hasta el que la "posición simulada" sigue abierta.
            # En vivo, execute_signal() descarta cualquier señal nueva mientras el
            # bot ya tiene una posición abierta (_has_open_position). Sin esto, el
            # backtest abría operaciones simuladas superpuestas sin límite en
            # estrategias de señal frecuente (ej. FVG generaba 400-1000+ "trades"
            # por año en un símbolo, la mayoría solapados entre sí) — inflaba
            # artificialmente tanto los resultados buenos como los catastróficos.
            blocked_until_idx = -1

            for i in range(warmup, total - 1):
                if i < blocked_until_idx:
                    continue  # posición simulada aún abierta — no evaluar nueva señal

                df_slice = df_full.iloc[:i+1].copy()
                signal = strategy.analyze(symbol, df_slice)

                if not signal:
                    continue

                direction = signal['direction']
                entry_bar = df_full.iloc[i+1]  # siguiente vela = entrada simulada

                # Obtener info del símbolo una sola vez por backtest (fuera del loop sería ideal
                # pero como puede fallar la incluimos aquí con guard)
                symbol_info = self.connector.get_symbol_info(symbol)
                if not symbol_info:
                    continue

                # Ajustar precio de entrada por spread real del instrumento.
                # Los datos OHLC de MT5 son precios bid. Para BUY la ejecución real
                # ocurre al ask (bid + spread), lo que reduce la ganancia potencial.
                # Para SELL se ejecuta al bid — sin ajuste de spread en la entrada.
                spread_cost = symbol_info.spread * symbol_info.point
                if direction == 'BUY':
                    entry = entry_bar['open'] + spread_cost
                else:
                    entry = entry_bar['open']

                # Comisión round-trip estimada (~$7 por lote estándar, típico Pepperstone)
                COMMISSION_RT_PER_LOT = 7.0

                # Calcular SL/TP usando ATR de las últimas 14 velas
                atr_series = self.market_analyzer.calculate_atr(df_slice.tail(20))
                atr = atr_series.iloc[-1] if not atr_series.empty else entry * 0.001

                # Usar multiplicadores base de la estrategia
                sl_mult = 2.0
                tp_mult = 3.0
                if strategy_type in ('EMA_CROSS', 'WILLIAMS_R', 'RSI'):
                    sl_mult, tp_mult = 1.5, 2.5
                elif strategy_type == 'BREAKOUT':
                    sl_mult, tp_mult = 2.0, 4.0
                elif strategy_type in ('LONDON_ORB', 'NY_ORB'):
                    sl_mult, tp_mult = 1.0, 1.5  # SL = rango, TP = 1.5x rango
                elif strategy_type == 'FVG':
                    sl_mult, tp_mult = 1.5, 2.5  # coincide con SL_ATR_MULT/TP_ATR_MULT reales

                if direction == 'BUY':
                    sl = entry - atr * sl_mult
                    tp = entry + atr * tp_mult
                else:
                    sl = entry + atr * sl_mult
                    tp = entry - atr * tp_mult

                # Calcular volumen (% del balance según risk_pct)
                risk_money  = balance * risk_pct
                risk_points = abs(entry - sl)
                if risk_points <= 0:
                    continue
                risk_per_lot = (risk_points / symbol_info.point) * symbol_info.tick_value
                if risk_per_lot <= 0:
                    continue
                volume = min(max(risk_money / risk_per_lot, symbol_info.volume_min), symbol_info.volume_max)
                # Normalizar al step permitido por el broker
                step   = symbol_info.volume_step
                volume = round(volume / step) * step if step > 0 else volume
                volume = max(symbol_info.volume_min, min(symbol_info.volume_max, volume))

                # Simular resultado mirando las siguientes velas (máx 50)
                result_pnl = None
                close_reason = 'timeout'
                exit_idx = min(i+51, total-1)  # default: timeout, se sobreescribe si SL/TP golpea antes
                for j in range(i+2, min(i+52, total)):
                    bar = df_full.iloc[j]
                    if direction == 'BUY':
                        if bar['low'] <= sl:
                            result_pnl  = -(risk_points / symbol_info.point) * symbol_info.tick_value * volume
                            close_reason = 'SL'
                            exit_idx = j
                            break
                        if bar['high'] >= tp:
                            reward      = abs(tp - entry)
                            result_pnl  = (reward / symbol_info.point) * symbol_info.tick_value * volume
                            close_reason = 'TP'
                            exit_idx = j
                            break
                    else:
                        if bar['high'] >= sl:
                            result_pnl  = -(risk_points / symbol_info.point) * symbol_info.tick_value * volume
                            close_reason = 'SL'
                            exit_idx = j
                            break
                        if bar['low'] <= tp:
                            reward      = abs(entry - tp)
                            result_pnl  = (reward / symbol_info.point) * symbol_info.tick_value * volume
                            close_reason = 'TP'
                            exit_idx = j
                            break

                # La posición simulada queda "abierta" hasta exit_idx — ninguna
                # señal nueva se evalúa antes de esa vela (ver blocked_until_idx).
                blocked_until_idx = exit_idx

                if result_pnl is None:
                    # Cerrar al precio de la última vela revisada
                    close_price = df_full.iloc[min(i+51, total-1)]['close']
                    if direction == 'BUY':
                        result_pnl = ((close_price - entry) / symbol_info.point) * symbol_info.tick_value * volume
                    else:
                        result_pnl = ((entry - close_price) / symbol_info.point) * symbol_info.tick_value * volume

                # Descontar comisión round-trip — refleja el costo real de cada trade
                result_pnl -= COMMISSION_RT_PER_LOT * volume

                balance += result_pnl
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100 if peak_balance > 0 else 0
                if dd > max_drawdown:
                    max_drawdown = dd

                trades.append({
                    'idx':       i,
                    'direction': direction,
                    'entry':     round(entry, 5),
                    'pnl':       round(result_pnl, 2),
                    'reason':    close_reason,
                    'balance':   round(balance, 2),
                })
                equity_curve.append({'balance': round(balance, 2), 'idx': len(trades)})

            # Métricas finales
            if not trades:
                return {
                    'symbol': symbol, 'strategy': strategy_type,
                    'days': days, 'trades': 0, 'message': 'Sin señales en el período'
                }

            wins        = [t for t in trades if t['pnl'] >= 0]
            losses      = [t for t in trades if t['pnl'] < 0]
            win_rate    = len(wins) / len(trades) * 100
            avg_win     = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
            avg_loss    = abs(sum(t['pnl'] for t in losses) / len(losses)) if losses else 0
            profit_factor = (sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses))
                             if losses and sum(t['pnl'] for t in losses) != 0 else 999)
            total_pnl   = balance - initial_balance

            logger.info(
                f"Backtest {strategy_type}/{symbol} ({days}d): "
                f"{len(trades)} trades, WR {win_rate:.1f}%, P&L ${total_pnl:.2f}"
            )

            return {
                'symbol':          symbol,
                'strategy':        strategy_type,
                'strategy_name':   self.STRATEGY_CATALOG.get(strategy_type, {}).get('name', strategy_type),
                'days':            days,
                'initial_balance': initial_balance,
                'final_balance':   round(balance, 2),
                'total_pnl':       round(total_pnl, 2),
                'total_pnl_pct':   round(total_pnl / initial_balance * 100, 2),
                'trades':          len(trades),
                'wins':            len(wins),
                'losses':          len(losses),
                'win_rate':        round(win_rate, 2),
                'avg_win':         round(avg_win, 2),
                'avg_loss':        round(avg_loss, 2),
                'profit_factor':   round(profit_factor, 2),
                'max_drawdown':    round(max_drawdown, 2),
                'equity_curve':    equity_curve[-100:],  # últimos 100 puntos para el gráfico
            }

        except Exception as e:
            logger.error(f"Error en backtest: {e}", exc_info=True)
            return {'error': str(e)}

    def stop_strategy(self, strategy_id: str) -> bool:
        if strategy_id in self.active_strategies:
            self.active_strategies[strategy_id].stop()
            del self.active_strategies[strategy_id]
            self._save_bots_config()
            return True
        return False
        
    def get_regime_status(self) -> Dict:
        """Retorna el estado actual de regímenes de mercado."""
        if not self.regime_detector:
            return {}
        return self.regime_detector.get_all_regimes()

    def force_regime_update(self) -> Dict:
        """Fuerza una re-evaluación inmediata de todos los regímenes."""
        if not self.regime_detector:
            return {}
        regimes = self.regime_detector.detect_all_regimes()
        changes = self.regime_detector.apply_regimes()
        return {'regimes': regimes, 'changes': changes}

    def get_strategies_status(self) -> List[Dict]:
        return [
            {
                "id": s_id,
                "name": strategy.name,
                "symbols": strategy.symbols,
                "is_running": strategy.is_running,
                "stats": strategy.get_statistics()
            }
            for s_id, strategy in self.active_strategies.items()
        ]