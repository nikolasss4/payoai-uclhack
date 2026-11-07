"""A small test shop for Purchase Checkout. It sells nothing real and takes no real payment."""

import uuid

from fastapi import FastAPI, HTTPException, Response

app = FastAPI(title="Sample Office Supplies (test shop)")

CATALOGUE = {"MON-27-4K": {"name": "27-inch 4K monitor", "price_minor": 21900}, "MON-24-FHD": {"name": "24-inch monitor", "price_minor": 13900}}
state = {"delivery_minor": 1500, "challenge": False, "price_override": None, "orders": {}, "carts": {}, "drop_order_response": False}


def _summary(cart: dict) -> dict:
    item = CATALOGUE[cart["sku"]]
    price = state["price_override"] if state["price_override"] is not None else item["price_minor"]
    subtotal = price * cart["quantity"]
    return {
        "merchant": "Sample Office Supplies",
        "domain": "shop.example.test",
        "sku": cart["sku"],
        "name": item["name"],
        "quantity": cart["quantity"],
        "subtotal_minor": subtotal,
        "tax_minor": 0,
        "delivery_minor": state["delivery_minor"],
        "total_minor": subtotal + state["delivery_minor"],
        "ship_to": cart["ship_to"],
    }


@app.get("/products/{sku}")
def product(sku: str):
    if sku not in CATALOGUE:
        raise HTTPException(404)
    item = CATALOGUE[sku]
    return Response(
        f"<html><body><h1>{item['name']}</h1><p>SKU {sku}</p><p>Price: £{item['price_minor'] / 100:.2f}</p>"
        f"<p>Delivery: £{state['delivery_minor'] / 100:.2f}</p></body></html>",
        media_type="text/html",
    )


@app.post("/cart")
def cart(body: dict):
    if body.get("sku") not in CATALOGUE:
        raise HTTPException(404)
    cart_id = uuid.uuid4().hex
    state["carts"][cart_id] = {"sku": body["sku"], "quantity": int(body.get("quantity", 1)), "ship_to": body["ship_to"]}
    return {"cart_id": cart_id}


@app.get("/checkout/{cart_id}")
def checkout(cart_id: str):
    if cart_id not in state["carts"]:
        raise HTTPException(404)
    if state["challenge"]:
        return {"challenge": "captcha"}
    return _summary(state["carts"][cart_id])


@app.post("/order/{cart_id}")
def order(cart_id: str, body: dict):
    if cart_id not in state["carts"]:
        raise HTTPException(404)
    if cart_id in state["orders"]:
        return state["orders"][cart_id]
    placed = {"order_number": f"SO-{uuid.uuid4().hex[:8].upper()}", "payment_status": "TAKEN", "total_minor": _summary(state["carts"][cart_id])["total_minor"]}
    state["orders"][cart_id] = placed
    if state["drop_order_response"]:
        raise HTTPException(503)
    return placed


@app.post("/admin/set")
def admin_set(body: dict):
    """Test control: change a price, delivery cost or challenge between steps."""
    state.update({k: v for k, v in body.items() if k in ("delivery_minor", "challenge", "price_override", "drop_order_response")})
    return {k: state[k] for k in ("delivery_minor", "challenge", "price_override", "drop_order_response")}


@app.get("/admin/orders")
def admin_orders():
    return {"count": len(state["orders"])}
