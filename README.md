# Framework de Trading MT5

Framework robusto y profesional para trading automatizado con MetaTrader 5, desarrollado en Python.

## 🚀 Características

- **Conexión robusta a MT5** con manejo de errores y reconexión automática
- **Gestión de órdenes** completa (abrir, cerrar, modificar posiciones)
- **Gestión de riesgo** avanzada con límites configurables
- **Análisis técnico** con múltiples indicadores (RSI, MACD, Bollinger Bands, ATR, etc.)
- **Sistema de estrategias** extensible y modular
- **Logging completo** para auditoría y debugging
- **Modelos de datos** validados con Pydantic
- **Configuración centralizada** mediante variables de entorno

## 📁 Estructura del Proyecto

```
MT5_trider/
├── config/                 # Configuración centralizada
│   ├── __init__.py
│   └── settings.py        # Settings y variables de entorno
├── core/                   # Módulos principales
│   ├── __init__.py
│   ├── order_manager.py   # Gestión de órdenes
│   ├── risk_manager.py    # Gestión de riesgo
│   └── market_analyzer.py # Análisis de mercado
├── models/                 # Modelos de datos
│   ├── __init__.py
│   └── trade_models.py    # Modelos Pydantic
├── platform_connector/     # Conector MT5
│   ├── __init__.py
│   └── platform_connector.py
├── strategies/             # Estrategias de trading
│   ├── __init__.py
│   ├── strategy_base.py   # Clase base
│   └── example_strategy.py # Estrategia de ejemplo
├── utils/                  # Utilidades
│   ├── __init__.py
│   ├── logger.py          # Sistema de logging
│   └── helpers.py         # Funciones auxiliares
├── logs/                   # Archivos de log (generado)
├── .env                    # Variables de entorno
├── requirements.txt        # Dependencias
├── trading_app.py         # Aplicación principal
└── README.md              # Este archivo
```

## 🔧 Instalación

### 1. Requisitos previos

- Python 3.8 o superior
- MetaTrader 5 instalado
- Cuenta de trading (demo o real)

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

Edita el archivo `.env` con tus credenciales:

```env
# Configuración de MT5
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=tu_numero_de_cuenta
MT5_PASSWORD=tu_contraseña
MT5_SERVER=nombre_del_servidor
MT5_TIMEOUT=60000
MT5_PORTABLE=False

# Configuración de logging
LOG_LEVEL=INFO
LOG_DIR=logs

# Configuración de trading
DEFAULT_DEVIATION=20
DEFAULT_MAGIC_NUMBER=234000
MAX_SLIPPAGE=10

# Gestión de riesgo
MAX_RISK_PER_TRADE=0.02
MAX_DAILY_LOSS=0.05
MAX_OPEN_POSITIONS=5
```

## 📖 Uso Básico

### Ejemplo 1: Análisis de Mercado

```python
from platform_connector.platform_connector import PlatformConnector
from core import MarketAnalyzer
import MetaTrader5 as mt5

# Conectar a MT5
connector = PlatformConnector(auto_connect=True)

# Crear analizador
analyzer = MarketAnalyzer(connector)

# Analizar mercado
analysis = analyzer.get_market_analysis("EURUSD", mt5.TIMEFRAME_H1)

print(f"Tendencia: {analysis['trend']}")
print(f"RSI: {analysis['indicators']['rsi']}")
print(f"Señal: {analysis['signals']['overall']}")

# Cerrar conexión
connector.disconnect()
```

### Ejemplo 2: Gestión de Órdenes

```python
from platform_connector.platform_connector import PlatformConnector
from core import OrderManager
from models import TradeRequest, OrderType

# Conectar
connector = PlatformConnector(auto_connect=True)
order_manager = OrderManager(connector)

# Crear solicitud de trading
request = TradeRequest(
    symbol="EURUSD",
    order_type=OrderType.BUY,
    volume=0.1,
    stop_loss=1.0950,
    take_profit=1.1050,
    comment="Operación de prueba"
)

# Abrir posición
result = order_manager.open_position(request)

if result.success:
    print(f"✅ Posición abierta: Ticket {result.ticket}")
else:
    print(f"❌ Error: {result.error_message}")

connector.disconnect()
```

### Ejemplo 3: Trading Automático con Estrategia

```python
from trading_app import TradingApp
import MetaTrader5 as mt5

# Crear aplicación
app = TradingApp()

# Inicializar
if app.initialize():
    # Configurar estrategia
    symbols = ["EURUSD", "GBPUSD"]
    if app.setup_strategy(symbols, timeframe=mt5.TIMEFRAME_H1):
        # Ejecutar trading automático
        app.run_trading_mode(iterations=10, interval=60)
    
    # Cerrar
    app.shutdown()
```

## 🎯 Componentes Principales

### PlatformConnector

Gestiona la conexión con MT5 y proporciona acceso a datos de mercado.

**Métodos principales:**
- `connect()` - Establece conexión
- `disconnect()` - Cierra conexión
- `get_account_info()` - Información de cuenta
- `get_positions()` - Posiciones abiertas
- `get_market_data()` - Datos de mercado actuales
- `get_historical_data()` - Datos históricos

### OrderManager

Gestiona la ejecución de órdenes de trading.

**Métodos principales:**
- `open_position()` - Abre nueva posición
- `close_position()` - Cierra posición
- `modify_position()` - Modifica SL/TP
- `close_all_positions()` - Cierra todas las posiciones

### RiskManager

Gestiona el riesgo y valida operaciones.

**Métodos principales:**
- `validate_trade()` - Valida operación según reglas de riesgo
- `calculate_position_size()` - Calcula tamaño óptimo de posición
- `is_trading_allowed()` - Verifica si se permite operar
- `get_risk_reward_ratio()` - Calcula ratio R:R

### MarketAnalyzer

Analiza mercado con indicadores técnicos.

**Métodos principales:**
- `get_market_analysis()` - Análisis completo
- `calculate_rsi()` - RSI
- `calculate_macd()` - MACD
- `calculate_bollinger_bands()` - Bandas de Bollinger
- `calculate_atr()` - ATR
- `detect_trend()` - Detecta tendencia

### StrategyBase

Clase base para crear estrategias personalizadas.

**Métodos a implementar:**
- `analyze()` - Analiza mercado y genera señales
- `calculate_entry_exit()` - Calcula precios de entrada/salida

## 🛡️ Gestión de Riesgo

El framework incluye múltiples capas de protección:

1. **Riesgo por operación**: Máximo 2% del balance por defecto
2. **Pérdida diaria máxima**: Máximo 5% del balance por defecto
3. **Límite de posiciones**: Máximo 5 posiciones simultáneas
4. **Validación de margen**: Verifica margen disponible
5. **Stop Loss obligatorio**: Para cálculo de riesgo

## 📊 Indicadores Disponibles

- **Medias Móviles**: SMA, EMA
- **Osciladores**: RSI, Estocástico
- **Tendencia**: MACD
- **Volatilidad**: Bandas de Bollinger, ATR
- **Niveles**: Soporte y Resistencia

## 🔍 Logging

El framework genera logs detallados en la carpeta `logs/`:

- Logs por módulo
- Rotación diaria automática
- Niveles: DEBUG, INFO, WARNING, ERROR
- Formato timestamp completo

## ⚠️ Advertencias Importantes

1. **Trading Real**: Este framework puede ejecutar operaciones reales. Prueba primero en cuenta demo.
2. **Gestión de Riesgo**: Configura límites apropiados en `.env`
3. **Monitoreo**: Supervisa el bot regularmente
4. **Backtesting**: Prueba estrategias antes de usar en real
5. **Responsabilidad**: El trading conlleva riesgos. Usa bajo tu responsabilidad.

## 🚀 Crear tu Propia Estrategia

```python
from strategies.strategy_base import StrategyBase
import pandas as pd

class MiEstrategia(StrategyBase):
    
    def analyze(self, symbol: str, df: pd.DataFrame):
        # Tu lógica de análisis
        # Retorna señal o None
        pass
    
    def calculate_entry_exit(self, symbol: str, signal: dict):
        # Calcula precios
        return {
            'entry': precio_entrada,
            'stop_loss': precio_sl,
            'take_profit': precio_tp
        }
```

## 📝 Próximas Mejoras

- [ ] Backtesting engine
- [ ] Optimización de parámetros
- [ ] Dashboard web
- [ ] Notificaciones por Telegram
- [ ] Más estrategias predefinidas
- [ ] Análisis de correlaciones
- [ ] Machine Learning integration

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Por favor:

1. Fork el proyecto
2. Crea una rama para tu feature
3. Commit tus cambios
4. Push a la rama
5. Abre un Pull Request

## 📄 Licencia

Este proyecto es de código abierto y está disponible bajo la licencia MIT.

## 📧 Contacto

Para preguntas o soporte, abre un issue en el repositorio.

---

**⚠️ DISCLAIMER**: Este software se proporciona "tal cual" sin garantías. El trading conlleva riesgos significativos. Nunca operes con dinero que no puedas permitirte perder.
