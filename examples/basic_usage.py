"""
Ejemplos básicos de uso del framework MT5
"""
import sys
from pathlib import Path

# Agregar el directorio raíz al path para importaciones
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

import MetaTrader5 as mt5
from platform_connector import PlatformConnector
from core import OrderManager, RiskManager, MarketAnalyzer
from models import TradeRequest, OrderType
from utils import get_logger, format_currency

logger = get_logger(__name__)

def ejemplo_1_conexion_basica():
    """Ejemplo 1: Conexión básica y obtención de información"""
    print("\n" + "="*60)
    print("EJEMPLO 1: Conexión Básica")
    print("="*60)
    
    # Conectar usando context manager (recomendado)
    with PlatformConnector() as connector:
        # Obtener información de cuenta
        account = connector.get_account_info()
        if account:
            print(f"✅ Conectado a cuenta: {account.login}")
            print(f"Balance: {format_currency(account.balance, account.currency)}")
            print(f"Equity: {format_currency(account.equity, account.currency)}")
            print(f"Margen libre: {format_currency(account.margin_free, account.currency)}")

def ejemplo_2_datos_mercado():
    """Ejemplo 2: Obtener datos de mercado"""
    print("\n" + "="*60)
    print("EJEMPLO 2: Datos de Mercado")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    
    # Obtener tick actual
    symbol = "EURUSD"
    market_data = connector.get_market_data(symbol)
    
    if market_data:
        print(f"\n{symbol}:")
        print(f"Bid: {market_data.bid}")
        print(f"Ask: {market_data.ask}")
        print(f"Spread: {market_data.spread:.5f}")
        print(f"Precio medio: {market_data.mid_price}")
    
    # Obtener información del símbolo
    symbol_info = connector.get_symbol_info(symbol)
    if symbol_info:
        print(f"\nInformación de {symbol}:")
        print(f"Descripción: {symbol_info.description}")
        print(f"Dígitos: {symbol_info.digits}")
        print(f"Volumen mínimo: {symbol_info.volume_min}")
        print(f"Volumen máximo: {symbol_info.volume_max}")
        print(f"Tamaño de contrato: {symbol_info.trade_contract_size}")
    
    connector.disconnect()

def ejemplo_3_datos_historicos():
    """Ejemplo 3: Obtener datos históricos"""
    print("\n" + "="*60)
    print("EJEMPLO 3: Datos Históricos")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    analyzer = MarketAnalyzer(connector)
    
    # Obtener últimas 50 velas H1
    symbol = "EURUSD"
    df = analyzer.get_candles(symbol, mt5.TIMEFRAME_H1, count=50)
    
    if df is not None and not df.empty:
        print(f"\n✅ Obtenidas {len(df)} velas de {symbol}")
        print(f"\nÚltimas 5 velas:")
        print(df[['time', 'open', 'high', 'low', 'close', 'tick_volume']].tail())
        
        # Calcular algunos indicadores
        df['sma_20'] = analyzer.calculate_sma(df, 20)
        df['rsi'] = analyzer.calculate_rsi(df)
        
        print(f"\nÚltimos valores:")
        print(f"Precio cierre: {df['close'].iloc[-1]}")
        print(f"SMA(20): {df['sma_20'].iloc[-1]:.5f}")
        print(f"RSI(14): {df['rsi'].iloc[-1]:.2f}")
    
    connector.disconnect()

def ejemplo_4_analisis_completo():
    """Ejemplo 4: Análisis completo de mercado"""
    print("\n" + "="*60)
    print("EJEMPLO 4: Análisis Completo")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    analyzer = MarketAnalyzer(connector)
    
    # Análisis completo
    symbol = "GBPUSD"
    analysis = analyzer.get_market_analysis(symbol, mt5.TIMEFRAME_H1)
    
    if analysis:
        print(f"\n📊 Análisis de {symbol}")
        print(f"Precio actual: {analysis['current_price']}")
        print(f"Tendencia: {analysis['trend']}")
        
        print("\nIndicadores:")
        ind = analysis['indicators']
        print(f"  RSI: {ind['rsi']:.2f}" if ind['rsi'] else "  RSI: N/A")
        print(f"  MACD: {ind['macd']:.5f}" if ind['macd'] else "  MACD: N/A")
        print(f"  ATR: {ind['atr']:.5f}" if ind['atr'] else "  ATR: N/A")
        
        print("\nSeñales:")
        for key, value in analysis['signals'].items():
            print(f"  {key}: {value}")
        
        print("\nNiveles:")
        print(f"  Resistencias: {analysis['levels']['resistances']}")
        print(f"  Soportes: {analysis['levels']['supports']}")
    
    connector.disconnect()

def ejemplo_5_posiciones():
    """Ejemplo 5: Ver posiciones abiertas"""
    print("\n" + "="*60)
    print("EJEMPLO 5: Posiciones Abiertas")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    
    positions = connector.get_positions()
    
    if positions:
        print(f"\n✅ {len(positions)} posiciones abiertas:")
        for pos in positions:
            print(f"\nTicket: {pos.ticket}")
            print(f"  {pos.symbol} - {pos.type}")
            print(f"  Volumen: {pos.volume}")
            print(f"  Precio apertura: {pos.price_open}")
            print(f"  Precio actual: {pos.price_current}")
            print(f"  Ganancia: {pos.profit:.2f}")
    else:
        print("\nNo hay posiciones abiertas")
    
    connector.disconnect()

def ejemplo_6_calculo_riesgo():
    """Ejemplo 6: Cálculo de tamaño de posición"""
    print("\n" + "="*60)
    print("EJEMPLO 6: Cálculo de Riesgo")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    risk_manager = RiskManager(connector)
    
    # Parámetros de ejemplo
    symbol = "EURUSD"
    entry_price = 1.1000
    stop_loss = 1.0950  # 50 pips de riesgo
    
    # Calcular tamaño de posición para 2% de riesgo
    volume = risk_manager.calculate_position_size(
        symbol,
        entry_price,
        stop_loss,
        risk_percentage=0.02
    )
    
    if volume:
        print(f"\n📊 Cálculo para {symbol}:")
        print(f"Precio entrada: {entry_price}")
        print(f"Stop Loss: {stop_loss}")
        print(f"Riesgo: 2% del balance")
        print(f"Tamaño calculado: {volume} lotes")
        
        # Calcular ratio R:R
        take_profit = 1.1100  # 100 pips de beneficio
        rr_ratio = risk_manager.get_risk_reward_ratio(
            entry_price, stop_loss, take_profit, is_buy=True
        )
        print(f"Take Profit: {take_profit}")
        print(f"Ratio R:R: 1:{rr_ratio:.2f}")
    
    connector.disconnect()

def ejemplo_7_validacion_operacion():
    """Ejemplo 7: Validar operación antes de ejecutar"""
    print("\n" + "="*60)
    print("EJEMPLO 7: Validación de Operación")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    risk_manager = RiskManager(connector)
    
    # Crear solicitud de ejemplo
    request = TradeRequest(
        symbol="EURUSD",
        order_type=OrderType.BUY,
        volume=0.1,
        price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        comment="Operación de prueba"
    )
    
    # Validar
    is_valid, message = risk_manager.validate_trade(request)
    
    print(f"\n📋 Validación de operación:")
    print(f"Símbolo: {request.symbol}")
    print(f"Tipo: {request.order_type}")
    print(f"Volumen: {request.volume}")
    print(f"Resultado: {'✅ APROBADA' if is_valid else '❌ RECHAZADA'}")
    print(f"Mensaje: {message}")
    
    # Mostrar estadísticas de riesgo
    stats = risk_manager.get_daily_stats()
    print(f"\nEstadísticas del día:")
    print(f"Trades: {stats['trades_count']}")
    print(f"Win Rate: {stats.get('win_rate', 0):.2f}%")
    
    connector.disconnect()

def ejemplo_8_simbolos_disponibles():
    """Ejemplo 8: Listar símbolos disponibles"""
    print("\n" + "="*60)
    print("EJEMPLO 8: Símbolos Disponibles")
    print("="*60)
    
    connector = PlatformConnector(auto_connect=True)
    
    symbols = connector.get_available_symbols()
    
    # Filtrar solo Forex
    forex_symbols = [s for s in symbols if any(pair in s for pair in ['EUR', 'USD', 'GBP', 'JPY'])]
    
    print(f"\n✅ Total de símbolos: {len(symbols)}")
    print(f"Símbolos Forex principales: {len(forex_symbols)}")
    print(f"\nPrimeros 20 símbolos Forex:")
    for symbol in forex_symbols[:20]:
        print(f"  - {symbol}")
    
    connector.disconnect()


if __name__ == "__main__":
    """Ejecutar todos los ejemplos"""
    
    print("\n" + "="*70)
    print(" EJEMPLOS DE USO DEL FRAMEWORK MT5 ".center(70, "="))
    print("="*70)
    
    try:
        # Ejecutar ejemplos (comenta los que no quieras ejecutar)
        ejemplo_1_conexion_basica()
        ejemplo_2_datos_mercado()
        ejemplo_3_datos_historicos()
        ejemplo_4_analisis_completo()
        ejemplo_5_posiciones()
        ejemplo_6_calculo_riesgo()
        ejemplo_7_validacion_operacion()
        ejemplo_8_simbolos_disponibles()
        
        print("\n" + "="*70)
        print(" EJEMPLOS COMPLETADOS ".center(70, "="))
        print("="*70 + "\n")
        
    except Exception as e:
        logger.error(f"Error en ejemplos: {str(e)}", exc_info=True)