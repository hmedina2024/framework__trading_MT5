# 📦 Guía de Instalación - Framework MT5

## Requisitos Previos

### 1. Software Necesario

- **Python 3.8 o superior** (recomendado 3.10+)
  - Descargar desde: https://www.python.org/downloads/
  - Durante la instalación, marcar "Add Python to PATH"

- **MetaTrader 5**
  - Descargar desde: https://www.metatrader5.com/es/download
  - Instalar y configurar con tu broker

- **Cuenta de Trading**
  - Cuenta demo (recomendado para pruebas)
  - O cuenta real (usar con precaución)

### 2. Verificar Instalación de Python

```bash
python --version
# Debe mostrar: Python 3.8.x o superior

pip --version
# Debe mostrar la versión de pip
```

## 🚀 Instalación Paso a Paso

### Paso 1: Clonar o Descargar el Proyecto

Si tienes el proyecto en un repositorio:
```bash
git clone <url-del-repositorio>
cd MT5_trider
```

O simplemente navega a la carpeta del proyecto.

### Paso 2: Crear Entorno Virtual (Recomendado)

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
```

Deberías ver `(venv)` al inicio de tu línea de comandos.

### Paso 3: Instalar Dependencias

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Esto instalará:
- `MetaTrader5` - Librería oficial de MT5
- `python-dotenv` - Gestión de variables de entorno
- `pydantic` - Validación de datos
- `pandas` - Análisis de datos
- `numpy` - Cálculos numéricos

### Paso 4: Verificar Instalación

```bash
python -c "import MetaTrader5 as mt5; print('MT5 version:', mt5.__version__)"
python -c "import pandas; print('Pandas version:', pandas.__version__)"
python -c "import pydantic; print('Pydantic version:', pydantic.__version__)"
```

## ⚙️ Configuración

### Paso 1: Configurar Variables de Entorno

Edita el archivo `.env` con tus credenciales:

```env
# Configuración de MT5
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=tu_contraseña
MT5_SERVER=NombreDelServidor-Demo
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

### Paso 2: Obtener tus Credenciales de MT5

1. Abre MetaTrader 5
2. Ve a **Herramientas → Opciones → Servidor**
3. Anota:
   - **Login**: Tu número de cuenta
   - **Servidor**: Nombre del servidor
   - **Contraseña**: Tu contraseña de trading

### Paso 3: Encontrar la Ruta de MT5

**Windows:**
- Ruta típica: `C:\Program Files\MetaTrader 5\terminal64.exe`
- O busca en: `C:\Program Files (x86)\MetaTrader 5\terminal64.exe`

**Para verificar:**
```bash
# Windows
where terminal64.exe

# O buscar manualmente en el explorador
```

## 🧪 Probar la Instalación

### Test 1: Conexión Básica

Crea un archivo `test_connection.py`:

```python
from platform_connector import PlatformConnector

# Intentar conectar
connector = PlatformConnector(auto_connect=True)

if connector.is_connected():
    print("✅ Conexión exitosa!")
    
    # Obtener info de cuenta
    account = connector.get_account_info()
    if account:
        print(f"Cuenta: {account.login}")
        print(f"Balance: {account.balance} {account.currency}")
    
    connector.disconnect()
else:
    print("❌ Error de conexión")
    print("Verifica tus credenciales en .env")
```

Ejecutar:
```bash
python test_connection.py
```

### Test 2: Ejecutar Ejemplos

```bash
python examples/basic_usage.py
```

### Test 3: Aplicación Principal

```bash
python trading_app.py
```

## 🔧 Solución de Problemas

### Error: "ModuleNotFoundError: No module named 'MetaTrader5'"

**Solución:**
```bash
pip install MetaTrader5
```

### Error: "Failed to initialize MT5"

**Posibles causas:**
1. **Ruta incorrecta de MT5**
   - Verifica `MT5_PATH` en `.env`
   - Asegúrate que apunta a `terminal64.exe`

2. **Credenciales incorrectas**
   - Verifica `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
   - Prueba primero con cuenta demo

3. **MT5 no está instalado**
   - Instala MetaTrader 5 primero

4. **Firewall bloqueando**
   - Permite MT5 en el firewall

### Error: "No module named 'dotenv'"

**Solución:**
```bash
pip install python-dotenv
```

### Error: "No module named 'pydantic'"

**Solución:**
```bash
pip install pydantic
```

### Error de Versión de Pydantic

Si usas Pydantic v1:
```bash
pip install "pydantic>=2.0.0"
```

### MT5 no se conecta en Linux/Mac

**Nota:** MetaTrader 5 es principalmente para Windows. En Linux/Mac:

**Opción 1: Wine**
```bash
# Instalar Wine
sudo apt-get install wine

# Ejecutar MT5 con Wine
wine terminal64.exe
```

**Opción 2: Máquina Virtual**
- Usar VirtualBox o VMware con Windows

**Opción 3: VPS Windows**
- Usar un VPS con Windows para ejecutar el bot

## 📊 Verificar que Todo Funciona

Ejecuta este script completo de verificación:

```python
# verify_installation.py
import sys

print("="*60)
print("VERIFICACIÓN DE INSTALACIÓN")
print("="*60)

# 1. Verificar Python
print(f"\n✓ Python version: {sys.version}")

# 2. Verificar librerías
try:
    import MetaTrader5 as mt5
    print(f"✓ MetaTrader5: {mt5.__version__}")
except ImportError as e:
    print(f"✗ MetaTrader5: {e}")

try:
    import pandas as pd
    print(f"✓ Pandas: {pd.__version__}")
except ImportError as e:
    print(f"✗ Pandas: {e}")

try:
    import numpy as np
    print(f"✓ Numpy: {np.__version__}")
except ImportError as e:
    print(f"✗ Numpy: {e}")

try:
    import pydantic
    print(f"✓ Pydantic: {pydantic.__version__}")
except ImportError as e:
    print(f"✗ Pydantic: {e}")

try:
    import dotenv
    print(f"✓ Python-dotenv: instalado")
except ImportError as e:
    print(f"✗ Python-dotenv: {e}")

# 3. Verificar estructura del proyecto
import os
required_dirs = ['config', 'core', 'models', 'platform_connector', 'strategies', 'utils']
print("\n" + "="*60)
print("ESTRUCTURA DEL PROYECTO")
print("="*60)
for dir_name in required_dirs:
    exists = os.path.isdir(dir_name)
    symbol = "✓" if exists else "✗"
    print(f"{symbol} {dir_name}/")

# 4. Verificar archivo .env
env_exists = os.path.isfile('.env')
print(f"\n{'✓' if env_exists else '✗'} Archivo .env")

print("\n" + "="*60)
if env_exists:
    print("Todo listo! Configura tus credenciales en .env")
else:
    print("Crea el archivo .env con tus credenciales")
print("="*60)
```

Ejecutar:
```bash
python verify_installation.py
```

## 🎯 Próximos Pasos

Una vez instalado correctamente:

1. **Configurar credenciales** en `.env`
2. **Probar conexión** con `test_connection.py`
3. **Ejecutar ejemplos** en `examples/basic_usage.py`
4. **Leer documentación** en `README.md`
5. **Estudiar arquitectura** en `ARCHITECTURE.md`
6. **Crear tu estrategia** basada en `strategies/example_strategy.py`

## 📚 Recursos Adicionales

- [Documentación MT5 Python](https://www.mql5.com/en/docs/python_metatrader5)
- [Pydantic Docs](https://docs.pydantic.dev/)
- [Pandas Docs](https://pandas.pydata.org/docs/)
- [README del Proyecto](README.md)
- [Arquitectura del Framework](ARCHITECTURE.md)

## 💬 Soporte

Si encuentras problemas:

1. Verifica que todas las dependencias estén instaladas
2. Revisa los logs en la carpeta `logs/`
3. Asegúrate que MT5 esté corriendo
4. Verifica las credenciales en `.env`
5. Prueba primero con cuenta demo

## ⚠️ Notas Importantes

- **Siempre prueba primero en cuenta DEMO**
- **Nunca compartas tu archivo .env**
- **Mantén backups de tu configuración**
- **Monitorea el bot regularmente**
- **El trading conlleva riesgos**
