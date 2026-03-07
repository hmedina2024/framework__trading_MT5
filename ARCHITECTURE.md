# Arquitectura del Framework MT5

## 📐 Visión General

Este framework sigue una arquitectura modular y en capas, diseñada para ser escalable, mantenible y fácil de extender.

## 🏗️ Capas de la Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                   CAPA DE APLICACIÓN                    │
│                    (trading_app.py)                     │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                  CAPA DE ESTRATEGIAS                    │
│              (strategies/strategy_base.py)              │
│           Lógica de trading y señales                   │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                    CAPA DE NEGOCIO                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │OrderManager  │  │RiskManager   │  │MarketAnalyzer│ │
│  │              │  │              │  │              │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                 CAPA DE CONECTIVIDAD                    │
│            (platform_connector/connector.py)            │
│              Comunicación con MT5                       │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                  CAPA DE DATOS/MODELOS                  │
│                  (models/trade_models.py)               │
│              Validación con Pydantic                    │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│              CAPA DE INFRAESTRUCTURA                    │
│    ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│    │Config        │  │Logger        │  │Helpers     │ │
│    │              │  │              │  │            │ │
│    └──────────────┘  └──────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## 📦 Componentes Principales

### 1. Platform Connector (Capa de Conectividad)

**Responsabilidad**: Gestionar la conexión con MetaTrader 5

**Características**:
- Conexión/desconexión robusta
- Manejo de errores y reconexión
- Obtención de datos de mercado
- Gestión de información de cuenta
- Context manager para uso seguro

**Archivos**:
- [`platform_connector/platform_connector.py`](platform_connector/platform_connector.py:1)

### 2. Core Modules (Capa de Negocio)

#### OrderManager
**Responsabilidad**: Gestión de órdenes y posiciones

**Funcionalidades**:
- Abrir posiciones (market/limit/stop)
- Cerrar posiciones
- Modificar SL/TP
- Validación de órdenes
- Normalización de precios y volúmenes

**Archivos**:
- [`core/order_manager.py`](core/order_manager.py:1)

#### RiskManager
**Responsabilidad**: Gestión de riesgo y capital

**Funcionalidades**:
- Validación de operaciones según reglas de riesgo
- Cálculo de tamaño de posición
- Límites de pérdida diaria
- Control de número de posiciones
- Estadísticas de trading

**Archivos**:
- [`core/risk_manager.py`](core/risk_manager.py:1)

#### MarketAnalyzer
**Responsabilidad**: Análisis técnico del mercado

**Funcionalidades**:
- Cálculo de indicadores técnicos (RSI, MACD, BB, ATR, etc.)
- Detección de tendencias
- Identificación de soportes/resistencias
- Generación de señales
- Análisis completo de mercado

**Archivos**:
- [`core/market_analyzer.py`](core/market_analyzer.py:1)

### 3. Strategy Layer (Capa de Estrategias)

**Responsabilidad**: Implementación de lógica de trading

**Patrón**: Template Method Pattern

**Componentes**:
- [`StrategyBase`](strategies/strategy_base.py:1): Clase abstracta base
- [`MovingAverageCrossStrategy`](strategies/example_strategy.py:1): Implementación ejemplo

**Flujo de ejecución**:
1. `analyze()` - Analiza mercado y genera señales
2. `calculate_entry_exit()` - Calcula precios
3. `execute_signal()` - Ejecuta operación
4. `check_exit_conditions()` - Verifica salidas

### 4. Models (Capa de Datos)

**Responsabilidad**: Definición y validación de datos

**Tecnología**: Pydantic

**Modelos principales**:
- [`TradeRequest`](models/trade_models.py:1): Solicitud de operación
- [`TradeResult`](models/trade_models.py:1): Resultado de operación
- [`Position`](models/trade_models.py:1): Posición abierta
- [`AccountInfo`](models/trade_models.py:1): Información de cuenta
- [`MarketData`](models/trade_models.py:1): Datos de mercado
- [`SymbolInfo`](models/trade_models.py:1): Información de símbolo

### 5. Infrastructure (Capa de Infraestructura)

#### Configuration
**Responsabilidad**: Gestión de configuración

**Características**:
- Variables de entorno con dotenv
- Validación de configuración
- Settings centralizados

**Archivos**:
- [`config/settings.py`](config/settings.py:1)

#### Logging
**Responsabilidad**: Sistema de logging

**Características**:
- Logs por módulo
- Múltiples niveles (DEBUG, INFO, WARNING, ERROR)
- Salida a consola y archivo
- Rotación diaria

**Archivos**:
- [`utils/logger.py`](utils/logger.py:1)

#### Helpers
**Responsabilidad**: Funciones auxiliares

**Funcionalidades**:
- Conversión de timeframes
- Formateo de datos
- Cálculos de trading
- Validaciones

**Archivos**:
- [`utils/helpers.py`](utils/helpers.py:1)

## 🔄 Flujo de Datos

### Flujo de Apertura de Posición

```
1. Usuario/Estrategia crea TradeRequest
                ↓
2. RiskManager valida la operación
                ↓
3. RiskManager calcula tamaño de posición
                ↓
4. OrderManager normaliza precios/volumen
                ↓
5. OrderManager envía orden a MT5
                ↓
6. MT5 ejecuta orden
                ↓
7. OrderManager retorna TradeResult
                ↓
8. Estrategia registra operación
```

### Flujo de Análisis de Mercado

```
1. Estrategia solicita análisis
                ↓
2. MarketAnalyzer obtiene datos históricos
                ↓
3. MarketAnalyzer calcula indicadores
                ↓
4. MarketAnalyzer detecta tendencia
                ↓
5. MarketAnalyzer genera señales
                ↓
6. Estrategia recibe análisis completo
                ↓
7. Estrategia decide acción
```

## 🎯 Patrones de Diseño Utilizados

### 1. **Singleton Pattern**
- **Dónde**: Settings, Logger
- **Por qué**: Una sola instancia de configuración y logger

### 2. **Template Method Pattern**
- **Dónde**: StrategyBase
- **Por qué**: Define esqueleto de algoritmo, subclases implementan pasos

### 3. **Context Manager Pattern**
- **Dónde**: PlatformConnector
- **Por qué**: Gestión automática de recursos (conexión/desconexión)

### 4. **Facade Pattern**
- **Dónde**: TradingApp
- **Por qué**: Interfaz simplificada para sistema complejo

### 5. **Strategy Pattern**
- **Dónde**: Strategies
- **Por qué**: Intercambio de algoritmos de trading

## 🔐 Principios SOLID

### Single Responsibility Principle (SRP)
- Cada clase tiene una única responsabilidad
- OrderManager: solo órdenes
- RiskManager: solo riesgo
- MarketAnalyzer: solo análisis

### Open/Closed Principle (OCP)
- Estrategias extensibles sin modificar base
- Nuevas estrategias heredan de StrategyBase

### Liskov Substitution Principle (LSP)
- Cualquier estrategia puede sustituir a StrategyBase
- Todas implementan misma interfaz

### Interface Segregation Principle (ISP)
- Interfaces específicas por funcionalidad
- No se fuerza implementación de métodos innecesarios

### Dependency Inversion Principle (DIP)
- Dependencias de abstracciones, no implementaciones
- Inyección de dependencias en constructores

## 🔒 Seguridad y Validación

### Validación de Datos
- Pydantic valida todos los modelos
- Tipos estrictos en Python
- Validación de rangos y formatos

### Gestión de Errores
- Try-catch en operaciones críticas
- Logging de errores detallado
- Mensajes de error descriptivos
- Códigos de error de MT5

### Gestión de Riesgo
- Múltiples capas de validación
- Límites configurables
- Verificación antes de cada operación
- Estadísticas en tiempo real

## 📊 Escalabilidad

### Horizontal
- Múltiples estrategias simultáneas
- Múltiples símbolos por estrategia
- Múltiples timeframes

### Vertical
- Optimización de cálculos con pandas/numpy
- Cache de datos cuando sea apropiado
- Logging asíncrono posible

## 🧪 Testing (Futuro)

### Estructura sugerida
```
tests/
├── unit/
│   ├── test_order_manager.py
│   ├── test_risk_manager.py
│   └── test_market_analyzer.py
├── integration/
│   ├── test_strategy_execution.py
│   └── test_mt5_connection.py
└── fixtures/
    └── sample_data.py
```

## 🚀 Extensibilidad

### Agregar Nueva Estrategia
1. Heredar de `StrategyBase`
2. Implementar `analyze()`
3. Implementar `calculate_entry_exit()`
4. Opcionalmente sobrescribir `check_exit_conditions()`

### Agregar Nuevo Indicador
1. Agregar método en `MarketAnalyzer`
2. Usar pandas/numpy para cálculos
3. Retornar pd.Series

### Agregar Nueva Validación de Riesgo
1. Agregar método en `RiskManager`
2. Llamar desde `validate_trade()`
3. Retornar bool y mensaje

## 📈 Performance

### Optimizaciones Implementadas
- Cálculos vectorizados con pandas/numpy
- Reutilización de conexión MT5
- Logging eficiente
- Validación temprana (fail-fast)

### Consideraciones
- Evitar llamadas excesivas a MT5
- Cache de symbol_info cuando sea posible
- Batch operations cuando sea apropiado

## 🔮 Roadmap Futuro

1. **Backtesting Engine**: Sistema completo de backtesting
2. **Optimization Module**: Optimización de parámetros
3. **Web Dashboard**: Interfaz web para monitoreo
4. **Database Integration**: Persistencia de datos
5. **Machine Learning**: Integración de ML para señales
6. **Multi-Broker**: Soporte para otros brokers
7. **Telegram Bot**: Notificaciones y control remoto
8. **Portfolio Management**: Gestión de múltiples cuentas

## 📚 Referencias

- [MetaTrader 5 Python Documentation](https://www.mql5.com/en/docs/python_metatrader5)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Pandas Documentation](https://pandas.pydata.org/docs/)
- Clean Architecture by Robert C. Martin
- Design Patterns by Gang of Four
