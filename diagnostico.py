"""
Diagnostico completo del sistema de trading.
Ejecutar en EC2: python diagnostico.py
"""
from platform_connector import PlatformConnector
from core import MarketAnalyzer, OrderManager, RiskManager
from strategies.bollinger_strategy import BollingerBandsStrategy
import MetaTrader5 as mt5
from datetime import datetime

print("=" * 60)
print("DIAGNOSTICO DEL SISTEMA")
print("=" * 60)

# 1. Conexion
conn = PlatformConnector(auto_connect=True)
print(f"\n1. Conexion MT5: {'OK' if conn.is_connected() else 'FALLA'}")

# 2. Datos de mercado
ma = MarketAnalyzer(conn)
om = OrderManager(conn)
rm = RiskManager(conn)

# 3. Verificar get_candles — la clave del fix
df = ma.get_candles('EURUSD', mt5.TIMEFRAME_H1, count=5)
print(f"\n2. get_candles EURUSD:")
print(f"   Ultima vela : {df['time'].iloc[-1]}")
print(f"   Hora local  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

diff_horas = (datetime.now() - df['time'].iloc[-1].to_pydatetime()).total_seconds() / 3600
print(f"   Diferencia  : {diff_horas:.1f} horas")

if diff_horas > 2:
    print("   PROBLEMA: La ultima vela tiene mas de 2h — fix de vela abierta NO esta activo")
    print("   => market_analyzer.py en EC2 es el version anterior")
else:
    print("   OK: Velas recientes, fix activo")

# 4. Verificar que hay senal disponible
print(f"\n3. Analisis de senales (ultimas velas cerradas):")
bot = BollingerBandsStrategy(
    connector=conn, order_manager=om, risk_manager=rm,
    market_analyzer=ma, symbols=['EURUSD'], magic_number=230001
)
df_full = ma.get_candles('EURUSD', mt5.TIMEFRAME_H1, count=50)
signal = bot.analyze('EURUSD', df_full)
print(f"   Bollinger EURUSD: {'SENAL ' + signal['direction'] if signal else 'sin senal'}")

# 5. Verificar run_iteration con log visible
print(f"\n4. run_iteration (debe mostrar logs de estrategia):")
import logging
logging.getLogger('strategies').setLevel(logging.INFO)
logging.getLogger('strategies.bollinger_strategy').setLevel(logging.INFO)
bot.run_iteration()
print("   run_iteration completada")

# 6. Verificar bots activos en el servidor
print(f"\n5. Para verificar bots activos, revisa en el frontend:")
print("   Estrategias > Bots Corriendo > cuantos aparecen?")

print("\n" + "=" * 60)
conn.disconnect()
