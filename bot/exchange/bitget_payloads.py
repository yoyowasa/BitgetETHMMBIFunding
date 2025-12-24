from __future__ import annotations


def spot_limit_ioc(symbol: str, side: str, price: float, size: float, client_oid: str) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "orderType": "limit",
        "force": "ioc",
        "price": str(price),
        "size": str(size),
        "clientOid": client_oid,
    }


def spot_limit_post_only(
    symbol: str, side: str, price: float, size: float, client_oid: str
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "orderType": "limit",
        "force": "post_only",
        "price": str(price),
        "size": str(size),
        "clientOid": client_oid,
    }


def spot_market_sell(symbol: str, size: float, client_oid: str) -> dict:
    return {
        "symbol": symbol,
        "side": "sell",
        "orderType": "market",
        "force": "gtc",
        "size": str(size),
        "clientOid": client_oid,
    }


def spot_market_buy(symbol: str, size: float, client_oid: str) -> dict:
    return {
        "symbol": symbol,
        "side": "buy",
        "orderType": "market",
        "force": "gtc",
        "size": str(size),
        "clientOid": client_oid,
    }


def spot_cancel_by_order_id(symbol: str, order_id: str) -> dict:
    return {
        "symbol": symbol,
        "orderId": order_id,
    }


def spot_cancel_by_client_oid(symbol: str, client_oid: str) -> dict:
    return {
        "symbol": symbol,
        "clientOid": client_oid,
    }


def perp_quote_post_only(
    symbol: str,
    product_type: str,
    margin_mode: str,
    margin_coin: str,
    side: str,
    price: float,
    size: float,
    client_oid: str,
) -> dict:
    return {
        "symbol": symbol,
        "productType": product_type,
        "marginMode": margin_mode,
        "marginCoin": margin_coin,
        "size": str(size),
        "price": str(price),
        "side": side,
        "orderType": "limit",
        "force": "post_only",
        "clientOid": client_oid,
    }


def perp_market(
    symbol: str,
    product_type: str,
    margin_mode: str,
    margin_coin: str,
    side: str,
    size: float,
    client_oid: str,
    reduce_only: bool = True,
) -> dict:
    return {
        "symbol": symbol,
        "productType": product_type,
        "marginMode": margin_mode,
        "marginCoin": margin_coin,
        "size": str(size),
        "side": side,
        "orderType": "market",
        "clientOid": client_oid,
        "reduceOnly": "YES" if reduce_only else "NO",
    }


def perp_cancel_by_order_id(
    order_id: str, symbol: str, product_type: str, margin_coin: str
) -> dict:
    return {
        "orderId": order_id,
        "symbol": symbol,
        "productType": product_type,
        "marginCoin": margin_coin,
    }
