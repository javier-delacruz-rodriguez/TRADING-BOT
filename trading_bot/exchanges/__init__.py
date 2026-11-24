from .base import ErrorExchange, EstadoOrden, Exchange, Filtros, InfoOrden, PostOnlyRechazada


def crear_exchange(cfg):
    """Crea el adaptador real del exchange configurado (import perezoso de la librería)."""
    if cfg.exchange == "bybit":
        from .bybit import BybitExchange
        return BybitExchange(cfg)
    if cfg.exchange == "binance":
        from .binance import BinanceExchange
        return BinanceExchange(cfg)
    raise ValueError(f"Exchange no soportado: {cfg.exchange}")


__all__ = [
    "EstadoOrden", "Exchange", "ErrorExchange", "Filtros", "InfoOrden", "PostOnlyRechazada", "crear_exchange",
]
