"""
Aplicación principal del Framework de Trading MT5

Este es un ejemplo completo de cómo usar el framework para trading automatizado.
"""
import MetaTrader5 as mt5
import time
from datetime import datetime

from platform_connector.platform_connector import PlatformConnector
from core import OrderManager, RiskManager, MarketAnalyzer
from strategies import MovingAverageCrossStrategy
from utils import get_logger, format_currency, timeframe_to_string
from config import settings

# Configurar logger
logger = get_logger(__name__)

class TradingApp:
    """
    Aplicación principal que coordina todos los componentes del framework
    """
    
    def __init__(self):
        """Inicializa la aplicación de trading"""
        logger.info("=" * 60)
        logger.info("Iniciando Framework de Trading MT5")
        logger.info("=" * 60)
        
        # Inicializar componentes
        self.connector = None
        self.order_manager = None
        self.risk_manager = None
        self.market_analyzer = None
        self.strategy = None
        
    def initialize(self) -> bool:
        """
        Inicializa todos los componentes del framework
        
        Returns:
            True si la inicialización fue exitosa
        """
        try:
            # 1. Conectar a MT5
            logger.info("Conectando a MetaTrader 5...")
            self.connector = PlatformConnector(auto_connect=True)
            
            if not self.connector.is_connected():
                logger.error("No se pudo establecer conexión con MT5")
                return False
            
            # 2. Mostrar información de cuenta
            account_info = self.connector.get_account_info()
            if account_info:
                logger.info(f"Cuenta: {account_info.login}")
                logger.info(f"Servidor: {account_info.server}")
                logger.info(f"Balance: {format_currency(account_info.balance, account_info.currency)}")
                logger.info(f"Equity: {format_currency(account_info.equity, account_info.currency)}")
                logger.info(f"Margen libre: {format_currency(account_info.margin_free, account_info.currency)}")
                logger.info(f"Apalancamiento: 1:{account_info.leverage}")
            
            # 3. Inicializar gestores
            logger.info("Inicializando gestores...")
            self.order_manager = OrderManager(self.connector)
            self.risk_manager = RiskManager(self.connector)
            self.market_analyzer = MarketAnalyzer(self.connector)
            
            logger.info("✅ Framework inicializado correctamente")
            return True
            
        except Exception as e:
            logger.error(f"Error al inicializar framework: {str(e)}", exc_info=True)
            return False
    
    def setup_strategy(
        self,
        symbols: list,
        timeframe: int = mt5.TIMEFRAME_H1
    ) -> bool:
        """
        Configura la estrategia de trading
        
        Args:
            symbols: Lista de símbolos a operar
            timeframe: Timeframe de la estrategia
            
        Returns:
            True si la configuración fue exitosa
        """
        try:
            logger.info(f"Configurando estrategia para {symbols}")
            logger.info(f"Timeframe: {timeframe_to_string(timeframe)}")
            
            # Verificar que los símbolos existan
            available_symbols = self.connector.get_available_symbols()
            for symbol in symbols:
                if symbol not in available_symbols:
                    logger.warning(f"⚠️ Símbolo {symbol} no disponible")
                    return False
            
            # Crear estrategia
            self.strategy = MovingAverageCrossStrategy(
                connector=self.connector,
                order_manager=self.order_manager,
                risk_manager=self.risk_manager,
                market_analyzer=self.market_analyzer,
                symbols=symbols,
                timeframe=timeframe,
                fast_period=12,
                slow_period=26,
                rsi_period=14,
                magic_number=settings.DEFAULT_MAGIC_NUMBER
            )
            
            logger.info("✅ Estrategia configurada correctamente")
            return True
            
        except Exception as e:
            logger.error(f"Error al configurar estrategia: {str(e)}", exc_info=True)
            return False
    
    def run_analysis_mode(self, symbol: str):
        """
        Ejecuta modo de análisis (sin trading real)
        
        Args:
            symbol: Símbolo a analizar
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"MODO ANÁLISIS - {symbol}")
        logger.info(f"{'='*60}\n")
        
        # Obtener análisis completo
        analysis = self.market_analyzer.get_market_analysis(
            symbol,
            timeframe=mt5.TIMEFRAME_H1,
            count=100
        )
        
        if not analysis:
            logger.error(f"No se pudo analizar {symbol}")
            return
        
        # Mostrar resultados
        logger.info(f"Símbolo: {analysis['symbol']}")
        logger.info(f"Precio actual: {analysis['current_price']}")
        logger.info(f"Tendencia: {analysis['trend']}")
        
        logger.info("\n--- Indicadores ---")
        indicators = analysis['indicators']
        logger.info(f"RSI: {indicators['rsi']:.2f}" if indicators['rsi'] else "RSI: N/A")
        logger.info(f"MACD: {indicators['macd']:.5f}" if indicators['macd'] else "MACD: N/A")
        logger.info(f"ATR: {indicators['atr']:.5f}" if indicators['atr'] else "ATR: N/A")
        
        logger.info("\n--- Señales ---")
        signals = analysis['signals']
        for key, value in signals.items():
            logger.info(f"{key.upper()}: {value}")
        
        logger.info("\n--- Niveles ---")
        levels = analysis['levels']
        logger.info(f"Resistencias: {levels['resistances']}")
        logger.info(f"Soportes: {levels['supports']}")
    
    def run_trading_mode(self, iterations: int = 10, interval: int = 60):
        """
        Ejecuta modo de trading automático
        
        Args:
            iterations: Número de iteraciones (-1 para infinito)
            interval: Intervalo entre iteraciones en segundos
        """
        if not self.strategy:
            logger.error("No hay estrategia configurada")
            return
        
        logger.info(f"\n{'='*60}")
        logger.info("MODO TRADING AUTOMÁTICO")
        logger.info(f"{'='*60}\n")
        logger.info(f"Iteraciones: {'Infinito' if iterations == -1 else iterations}")
        logger.info(f"Intervalo: {interval} segundos")
        
        self.strategy.start()
        
        iteration = 0
        try:
            while iterations == -1 or iteration < iterations:
                iteration += 1
                logger.info(f"\n--- Iteración {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
                
                # Verificar si se permite operar
                allowed, reason = self.risk_manager.is_trading_allowed()
                if not allowed:
                    logger.warning(f"⚠️ Trading no permitido: {reason}")
                    break
                
                # Ejecutar iteración de estrategia
                self.strategy.run_iteration()
                
                # Mostrar estadísticas
                stats = self.strategy.get_statistics()
                logger.info(f"\nEstadísticas:")
                logger.info(f"Balance: {format_currency(stats['account_balance'])}")
                logger.info(f"Equity: {format_currency(stats['account_equity'])}")
                logger.info(f"Posiciones abiertas: {stats['open_positions']}")
                logger.info(f"Trades del día: {stats['daily_stats']['trades_count']}")
                
                if stats['daily_stats']['trades_count'] > 0:
                    logger.info(f"Win Rate: {stats['daily_stats']['win_rate']:.2f}%")
                    logger.info(f"P&L del día: {stats['daily_stats']['total_profit']:.2f}")
                
                # Esperar antes de siguiente iteración
                if iterations == -1 or iteration < iterations:
                    logger.info(f"\nEsperando {interval} segundos...")
                    time.sleep(interval)
                    
        except KeyboardInterrupt:
            logger.info("\n⚠️ Detenido por usuario")
        except Exception as e:
            logger.error(f"Error en modo trading: {str(e)}", exc_info=True)
        finally:
            self.strategy.stop()
            logger.info("Estrategia detenida")
    
    def show_positions(self):
        """Muestra las posiciones abiertas"""
        logger.info(f"\n{'='*60}")
        logger.info("POSICIONES ABIERTAS")
        logger.info(f"{'='*60}\n")
        
        positions = self.connector.get_positions()
        
        if not positions:
            logger.info("No hay posiciones abiertas")
            return
        
        for pos in positions:
            logger.info(f"Ticket: {pos.ticket}")
            logger.info(f"  Símbolo: {pos.symbol}")
            logger.info(f"  Tipo: {pos.type}")
            logger.info(f"  Volumen: {pos.volume}")
            logger.info(f"  Precio apertura: {pos.price_open}")
            logger.info(f"  Precio actual: {pos.price_current}")
            logger.info(f"  Stop Loss: {pos.stop_loss}")
            logger.info(f"  Take Profit: {pos.take_profit}")
            logger.info(f"  Ganancia: {pos.profit:.2f}")
            logger.info(f"  Comentario: {pos.comment}")
            logger.info("")
    
    def shutdown(self):
        """Cierra la aplicación correctamente"""
        logger.info("\nCerrando aplicación...")
        
        if self.strategy and self.strategy.is_running:
            self.strategy.stop()
        
        if self.connector:
            self.connector.disconnect()
        
        logger.info("✅ Aplicación cerrada correctamente")


def main():
    """Función principal"""
    
    # Crear aplicación
    app = TradingApp()
    
    # Inicializar
    if not app.initialize():
        logger.error("Error al inicializar aplicación")
        return
    
    # Ejemplo 1: Modo análisis
    logger.info("\n" + "="*60)
    logger.info("EJEMPLO 1: ANÁLISIS DE MERCADO")
    logger.info("="*60)
    app.run_analysis_mode("EURUSD")
    
    # Ejemplo 2: Mostrar posiciones
    logger.info("\n" + "="*60)
    logger.info("EJEMPLO 2: POSICIONES ACTUALES")
    logger.info("="*60)
    app.show_positions()
    
    # Ejemplo 3: Trading automático (comentado por seguridad)
    # Descomentar para activar trading real
    """
    logger.info("\n" + "="*60)
    logger.info("EJEMPLO 3: TRADING AUTOMÁTICO")
    logger.info("="*60)
    
    # Configurar estrategia
    symbols = ["EURUSD", "GBPUSD"]
    if app.setup_strategy(symbols, timeframe=mt5.TIMEFRAME_H1):
        # Ejecutar 5 iteraciones con intervalo de 60 segundos
        app.run_trading_mode(iterations=5, interval=60)
    """
    
    # Cerrar aplicación
    app.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Error fatal: {str(e)}", exc_info=True)
