"""
Script simple para probar la conexión a MT5
"""
from platform_connector import PlatformConnector
from utils import get_logger, format_currency

logger = get_logger(__name__)

def main():
    print("\n" + "="*60)
    print(" TEST DE CONEXIÓN MT5 ".center(60, "="))
    print("="*60 + "\n")
    
    # Intentar conectar
    print("Intentando conectar a MetaTrader 5...")
    connector = PlatformConnector(auto_connect=True)
    
    if connector.is_connected():
        print("\n✅ ¡CONEXIÓN EXITOSA!\n")
        
        # Obtener información de cuenta
        account = connector.get_account_info()
        if account:
            print("Información de la Cuenta:")
            print("-" * 60)
            print(f"Login:          {account.login}")
            print(f"Servidor:       {account.server}")
            print(f"Compañía:       {account.company}")
            print(f"Moneda:         {account.currency}")
            print(f"Apalancamiento: 1:{account.leverage}")
            print(f"\nBalance:        {format_currency(account.balance, account.currency)}")
            print(f"Equity:         {format_currency(account.equity, account.currency)}")
            print(f"Margen usado:   {format_currency(account.margin, account.currency)}")
            print(f"Margen libre:   {format_currency(account.margin_free, account.currency)}")
            print(f"Nivel margen:   {account.margin_level:.2f}%")
            print(f"Ganancia:       {format_currency(account.profit, account.currency)}")
        
        # Obtener posiciones abiertas
        print("\n" + "-" * 60)
        positions = connector.get_positions()
        print(f"Posiciones abiertas: {len(positions)}")
        
        if positions:
            print("\nDetalle de posiciones:")
            for pos in positions:
                print(f"\n  Ticket: {pos.ticket}")
                print(f"  Símbolo: {pos.symbol}")
                print(f"  Tipo: {pos.type}")
                print(f"  Volumen: {pos.volume}")
                print(f"  Precio apertura: {pos.price_open}")
                print(f"  Precio actual: {pos.price_current}")
                print(f"  Ganancia: {pos.profit:.2f}")
        
        # Obtener algunos símbolos disponibles
        print("\n" + "-" * 60)
        symbols = connector.get_available_symbols()
        print(f"Símbolos disponibles: {len(symbols)}")
        
        # Filtrar símbolos Forex principales
        forex_symbols = [s for s in symbols if any(pair in s for pair in ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD'])]
        if forex_symbols:
            print(f"\nAlgunos pares Forex disponibles:")
            for symbol in forex_symbols[:10]:
                print(f"  - {symbol}")
        
        # Obtener datos de mercado de EURUSD
        print("\n" + "-" * 60)
        market_data = connector.get_market_data("EURUSD")
        if market_data:
            print("Datos de mercado EURUSD:")
            print(f"  Bid: {market_data.bid}")
            print(f"  Ask: {market_data.ask}")
            print(f"  Spread: {market_data.spread:.5f}")
            print(f"  Último: {market_data.last}")
        
        # Cerrar conexión
        connector.disconnect()
        print("\n" + "="*60)
        print("✅ Test completado exitosamente")
        print("="*60 + "\n")
        
    else:
        print("\n❌ ERROR: No se pudo conectar a MT5\n")
        print("Posibles causas:")
        print("1. Credenciales incorrectas en el archivo .env")
        print("2. MetaTrader 5 no está instalado")
        print("3. Ruta de MT5 incorrecta en .env")
        print("4. Servidor no disponible")
        print("\nVerifica tu archivo .env y asegúrate de que:")
        print("- MT5_PATH apunta a terminal64.exe")
        print("- MT5_LOGIN es tu número de cuenta")
        print("- MT5_PASSWORD es tu contraseña")
        print("- MT5_SERVER es el nombre correcto del servidor")
        print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Error en test de conexión: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}\n")
