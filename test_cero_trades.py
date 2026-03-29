"""
Verifica estrategias con 0 trades — determina si es falta de señal o error.
Ejecutar: python test_cero_trades.py
"""
from platform_connector import PlatformConnector
from core import MarketAnalyzer, OrderManager, RiskManager
from strategies.supertrend_strategy import SupertrendStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.breakout_strategy import BreakoutStrategy
from strategies.ema_crossover_strategy import EMACrossoverStrategy
import MetaTrader5 as mt5
import traceback

conn = PlatformConnector(auto_connect=True)
ma   = MarketAnalyzer(conn)
om   = OrderManager(conn)
rm   = RiskManager(conn)

# Estrategias con 0 trades según el dashboard
tests = [
    ('SUPERTREND', SupertrendStrategy,  'USDJPY', mt5.TIMEFRAME_H1, 260003),
    ('SUPERTREND', SupertrendStrategy,  'USDCAD', mt5.TIMEFRAME_H1, 260006),
    ('MACD',       MACDStrategy,        'EURUSD', mt5.TIMEFRAME_H1, 240001),
    ('BREAKOUT',   BreakoutStrategy,    'US30',   mt5.TIMEFRAME_H4, 250007),
    ('BREAKOUT',   BreakoutStrategy,    'BTCUSD', mt5.TIMEFRAME_H4, 250008),
    ('EMA_CROSS',  EMACrossoverStrategy,'USDJPY', mt5.TIMEFRAME_H1, 270003),
]

print("=" * 65)
print(f"{'ESTRATEGIA':<14} {'SIMBOLO':<8} {'ESTADO'}")
print("=" * 65)

for strat_name, StratClass, symbol, tf, magic in tests:
    try:
        bot = StratClass(
            connector=conn, order_manager=om, risk_manager=rm,
            market_analyzer=ma, symbols=[symbol], magic_number=magic
        )

        # Intentar obtener velas
        df = ma.get_candles(symbol, tf, count=200)
        if df is None or df.empty:
            print(f"{strat_name:<14} {symbol:<8} ERROR: get_candles retornó None")
            continue

        print(f"{strat_name:<14} {symbol:<8} OK: {len(df)} velas obtenidas", end="")

        # Intentar analizar
        signal = bot.analyze(symbol, df)
        if signal:
            print(f" | SEÑAL ACTIVA: {signal['direction']}")
        else:
            # Ver últimas 5 velas para entender el mercado
            last = df.iloc[-1]
            prev = df.iloc[-2]

            # Calcular EMA200 para ver tendencia
            ema200 = ma.calculate_ema(df, 200)
            price  = last['close']
            ema    = ema200.iloc[-1]
            trend  = 'ALCISTA' if price > ema else 'BAJISTA'

            print(f" | sin señal | precio={price:.4f} EMA200={ema:.4f} tendencia={trend}")

    except Exception as e:
        print(f"{strat_name:<14} {symbol:<8} EXCEPCION: {str(e)}")
        traceback.print_exc()

print("=" * 65)

# Verificar también que los timeframes H4 devuelven datos para Breakout
print("\nVerificando datos H4 para Breakout:")
for sym in ['US30', 'BTCUSD', 'XAUUSD']:
    df4 = ma.get_candles(sym, mt5.TIMEFRAME_H4, count=50)
    if df4 is None or df4.empty:
        print(f"  {sym}: ERROR — sin datos H4")
    else:
        print(f"  {sym}: OK — {len(df4)} velas H4 | ultima: {df4['time'].iloc[-1]}")

conn.disconnect()