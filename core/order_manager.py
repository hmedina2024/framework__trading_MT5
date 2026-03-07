"""
Gestor de órdenes para operaciones de trading en MT5
"""
import MetaTrader5 as mt5
from typing import Optional, List
from datetime import datetime

from utils.logger import get_logger
from models.trade_models import (
    TradeRequest, TradeResult, OrderType, Position
)
from config.settings import settings

logger = get_logger(__name__)

class OrderManager:
    """
    Gestor completo de órdenes de trading con validación,
    ejecución y seguimiento de operaciones.
    """
    
    def __init__(self, connector):
        """
        Inicializa el gestor de órdenes
        
        Args:
            connector: Instancia de PlatformConnector
        """
        self.connector = connector
        logger.info("OrderManager inicializado")
    
    def _validate_trade_request(self, request: TradeRequest) -> tuple[bool, str]:
        """
        Valida una solicitud de trading
        
        Args:
            request: Solicitud de trading a validar
            
        Returns:
            Tupla (es_válido, mensaje_error)
        """
        # Verificar conexión
        if not self.connector.is_connected():
            return False, "No hay conexión con MT5"
        
        # Verificar símbolo
        symbol_info = self.connector.get_symbol_info(request.symbol)
        if not symbol_info:
            return False, f"Símbolo {request.symbol} no disponible"
        
        # Validar volumen
        if request.volume < symbol_info.volume_min:
            return False, f"Volumen {request.volume} menor que mínimo {symbol_info.volume_min}"
        
        if request.volume > symbol_info.volume_max:
            return False, f"Volumen {request.volume} mayor que máximo {symbol_info.volume_max}"
        
        # Normalizar volumen
        request.volume = symbol_info.normalize_volume(request.volume)
        
        # Validar precios si están presentes
        if request.price:
            request.price = symbol_info.normalize_price(request.price)
        
        if request.stop_loss:
            request.stop_loss = symbol_info.normalize_price(request.stop_loss)
        
        if request.take_profit:
            request.take_profit = symbol_info.normalize_price(request.take_profit)
        
        return True, ""
    
    def _map_order_type(self, order_type: OrderType) -> int:
        """
        Mapea OrderType a constantes de MT5
        
        Args:
            order_type: Tipo de orden
            
        Returns:
            Constante de MT5
        """
        mapping = {
            OrderType.BUY: mt5.ORDER_TYPE_BUY,
            OrderType.SELL: mt5.ORDER_TYPE_SELL,
            OrderType.BUY_LIMIT: mt5.ORDER_TYPE_BUY_LIMIT,
            OrderType.SELL_LIMIT: mt5.ORDER_TYPE_SELL_LIMIT,
            OrderType.BUY_STOP: mt5.ORDER_TYPE_BUY_STOP,
            OrderType.SELL_STOP: mt5.ORDER_TYPE_SELL_STOP,
            OrderType.BUY_STOP_LIMIT: mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            OrderType.SELL_STOP_LIMIT: mt5.ORDER_TYPE_SELL_STOP_LIMIT,
        }
        return mapping.get(order_type, mt5.ORDER_TYPE_BUY)
    
    def open_position(self, request: TradeRequest) -> TradeResult:
        """
        Abre una nueva posición
        
        Args:
            request: Solicitud de trading
            
        Returns:
            TradeResult con el resultado de la operación
        """
        logger.info(f"Intentando abrir posición: {request.symbol} {request.order_type} {request.volume}")
        
        # Validar solicitud
        is_valid, error_msg = self._validate_trade_request(request)
        if not is_valid:
            logger.error(f"Validación fallida: {error_msg}")
            return TradeResult(
                success=False,
                error_message=error_msg
            )
        
        try:
            # Obtener precio actual si no se especificó
            if not request.price:
                market_data = self.connector.get_market_data(request.symbol)
                if not market_data:
                    return TradeResult(
                        success=False,
                        error_message="No se pudo obtener precio de mercado"
                    )
                
                # Usar ask para compra, bid para venta
                if request.order_type in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
                    request.price = market_data.ask
                else:
                    request.price = market_data.bid
            
            # Preparar solicitud para MT5
            mt5_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": request.symbol,
                "volume": request.volume,
                "type": self._map_order_type(request.order_type),
                "price": request.price,
                "deviation": request.deviation,
                "magic": request.magic_number,
                "comment": request.comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Agregar SL/TP si están presentes
            if request.stop_loss:
                mt5_request["sl"] = request.stop_loss
            
            if request.take_profit:
                mt5_request["tp"] = request.take_profit
            
            # Enviar orden
            result = mt5.order_send(mt5_request)
            
            if result is None:
                error = mt5.last_error()
                logger.error(f"Error al enviar orden: {error}")
                return TradeResult(
                    success=False,
                    error_code=error[0] if error else None,
                    error_message=str(error)
                )
            
            # Procesar resultado
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Posición abierta exitosamente. Ticket: {result.order}")
                return TradeResult(
                    success=True,
                    order_id=result.order,
                    ticket=result.order,
                    volume=result.volume,
                    price=result.price
                )
            else:
                logger.warning(f"Orden rechazada. Código: {result.retcode}, Comentario: {result.comment}")
                return TradeResult(
                    success=False,
                    error_code=result.retcode,
                    error_message=result.comment
                )
                
        except Exception as e:
            logger.error(f"Error inesperado al abrir posición: {str(e)}", exc_info=True)
            return TradeResult(
                success=False,
                error_message=f"Error inesperado: {str(e)}"
            )
    
    def close_position(
        self,
        ticket: int,
        volume: Optional[float] = None,
        deviation: Optional[int] = None
    ) -> TradeResult:
        """
        Cierra una posición existente
        
        Args:
            ticket: Ticket de la posición a cerrar
            volume: Volumen a cerrar (None para cerrar completamente)
            deviation: Desviación permitida
            
        Returns:
            TradeResult con el resultado de la operación
        """
        logger.info(f"Intentando cerrar posición: Ticket {ticket}")
        
        if not self.connector.is_connected():
            return TradeResult(
                success=False,
                error_message="No hay conexión con MT5"
            )
        
        try:
            # Obtener información de la posición
            position = mt5.positions_get(ticket=ticket)
            if not position or len(position) == 0:
                logger.error(f"Posición {ticket} no encontrada")
                return TradeResult(
                    success=False,
                    error_message=f"Posición {ticket} no encontrada"
                )
            
            pos = position[0]
            
            # Determinar volumen a cerrar
            close_volume = volume if volume else pos.volume
            
            # Determinar tipo de orden de cierre (opuesto a la posición)
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            
            # Obtener precio actual
            market_data = self.connector.get_market_data(pos.symbol)
            if not market_data:
                return TradeResult(
                    success=False,
                    error_message="No se pudo obtener precio de mercado"
                )
            
            close_price = market_data.bid if pos.type == mt5.ORDER_TYPE_BUY else market_data.ask
            
            # Preparar solicitud de cierre
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": close_volume,
                "type": close_type,
                "position": ticket,
                "price": close_price,
                "deviation": deviation if deviation else settings.DEFAULT_DEVIATION,
                "magic": pos.magic,
                "comment": "Cierre de posición",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Enviar orden de cierre
            result = mt5.order_send(close_request)
            
            if result is None:
                error = mt5.last_error()
                logger.error(f"Error al cerrar posición: {error}")
                return TradeResult(
                    success=False,
                    error_code=error[0] if error else None,
                    error_message=str(error)
                )
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Posición {ticket} cerrada exitosamente")
                return TradeResult(
                    success=True,
                    order_id=result.order,
                    ticket=ticket,
                    volume=result.volume,
                    price=result.price
                )
            else:
                logger.warning(f"Cierre rechazado. Código: {result.retcode}, Comentario: {result.comment}")
                return TradeResult(
                    success=False,
                    error_code=result.retcode,
                    error_message=result.comment
                )
                
        except Exception as e:
            logger.error(f"Error inesperado al cerrar posición: {str(e)}", exc_info=True)
            return TradeResult(
                success=False,
                error_message=f"Error inesperado: {str(e)}"
            )
    
    def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> TradeResult:
        """
        Modifica SL/TP de una posición existente
        
        Args:
            ticket: Ticket de la posición
            stop_loss: Nuevo stop loss (None para no modificar)
            take_profit: Nuevo take profit (None para no modificar)
            
        Returns:
            TradeResult con el resultado de la operación
        """
        logger.info(f"Modificando posición {ticket}: SL={stop_loss}, TP={take_profit}")
        
        if not self.connector.is_connected():
            return TradeResult(
                success=False,
                error_message="No hay conexión con MT5"
            )
        
        try:
            # Obtener posición actual
            position = mt5.positions_get(ticket=ticket)
            if not position or len(position) == 0:
                return TradeResult(
                    success=False,
                    error_message=f"Posición {ticket} no encontrada"
                )
            
            pos = position[0]
            
            # Preparar solicitud de modificación
            modify_request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": ticket,
                "sl": stop_loss if stop_loss is not None else pos.sl,
                "tp": take_profit if take_profit is not None else pos.tp,
            }
            
            # Enviar modificación
            result = mt5.order_send(modify_request)
            
            if result is None:
                error = mt5.last_error()
                logger.error(f"Error al modificar posición: {error}")
                return TradeResult(
                    success=False,
                    error_code=error[0] if error else None,
                    error_message=str(error)
                )
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Posición {ticket} modificada exitosamente")
                return TradeResult(
                    success=True,
                    ticket=ticket
                )
            else:
                logger.warning(f"Modificación rechazada. Código: {result.retcode}")
                return TradeResult(
                    success=False,
                    error_code=result.retcode,
                    error_message=result.comment
                )
                
        except Exception as e:
            logger.error(f"Error al modificar posición: {str(e)}", exc_info=True)
            return TradeResult(
                success=False,
                error_message=f"Error inesperado: {str(e)}"
            )
    
    def close_all_positions(self, symbol: Optional[str] = None) -> List[TradeResult]:
        """
        Cierra todas las posiciones abiertas
        
        Args:
            symbol: Filtrar por símbolo (opcional)
            
        Returns:
            Lista de TradeResult con los resultados
        """
        logger.info(f"Cerrando todas las posiciones{f' de {symbol}' if symbol else ''}")
        
        positions = self.connector.get_positions(symbol)
        results = []
        
        for position in positions:
            result = self.close_position(position.ticket)
            results.append(result)
        
        successful = sum(1 for r in results if r.success)
        logger.info(f"Cerradas {successful}/{len(results)} posiciones")
        
        return results
