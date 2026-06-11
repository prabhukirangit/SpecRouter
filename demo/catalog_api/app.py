"""Acme Product Catalog — a local FastAPI app exposing ~100 distinct product endpoints.

Each (category, operation) pair is registered as its own spec operation with the category
baked literally into the path + operation_id + summary, so SpecRouter sees ~100 *separate*
endpoints to discover and rank (the whole point of the demo) — not a handful of
``{category}``-parameterised routes.

Run it::

    uvicorn demo.catalog_api.app:app --port 8000      # then GET http://localhost:8000/openapi.json
    python -m demo.catalog_api.app                     # equivalent (reads $CATALOG_PORT)

Then point SpecRouter at ``http://localhost:8000/openapi.json`` (see demo/README.md).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from .data import (
    CATALOG,
    CATEGORIES,
    CartLine,
    CartRequest,
    Deal,
    Inventory,
    PriceBreakdown,
    Product,
    ProductDetail,
    Review,
    get_product,
)

_PORT = os.getenv("CATALOG_PORT", "8000")
_HOST = os.getenv("CATALOG_HOST", "localhost")
BASE_URL = os.getenv("CATALOG_BASE_URL", f"http://{_HOST}:{_PORT}")

app = FastAPI(
    title="Acme Product Catalog",
    version="1.0.0",
    description="A demo storefront API: ~100 product endpoints across 10 categories.",
    # Absolute servers[] so SpecRouter auto-resolves the base URL from the spec.
    servers=[{"url": BASE_URL, "description": "Local demo server"}],
)


def _require(category: str, product_id: str) -> ProductDetail:
    product = get_product(category, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"No product '{product_id}' in '{category}'.")
    return product


def _register_category(category: str) -> None:
    """Register the ~10 operations for one category (literal category in every path)."""
    meta = CATEGORIES[category]
    label = meta["label"]
    base = f"/catalog/{category}"

    # --- collection reads ---------------------------------------------------
    def list_products() -> list[Product]:
        return [Product(**p.model_dump()) for p in CATALOG[category]]

    def featured() -> list[Product]:
        return [Product(**p.model_dump()) for p in CATALOG[category][:3]]

    def bestsellers() -> list[Product]:
        ranked = sorted(CATALOG[category], key=lambda p: p.rating, reverse=True)
        return [Product(**p.model_dump()) for p in ranked[:5]]

    def deals() -> list[Deal]:
        out: list[Deal] = []
        for p in CATALOG[category]:
            if not p.in_stock:
                continue
            now = round(p.price * 0.8, 2)
            out.append(Deal(product_id=p.id, name=p.name, was=p.price, now=now, ends_in_days=3))
        return out

    def inventory() -> list[Inventory]:
        return [
            Inventory(product_id=p.id, warehouse="WH-1", quantity=(0 if not p.in_stock else 42))
            for p in CATALOG[category]
        ]

    # --- per-product reads --------------------------------------------------
    def product_detail(product_id: str) -> ProductDetail:
        return _require(category, product_id)

    def product_specs(product_id: str) -> dict:
        return _require(category, product_id).attributes

    def product_reviews(product_id: str) -> list[Review]:
        p = _require(category, product_id)
        return [
            Review(author="A. Buyer", rating=5, title="Great", body=f"Love this {p.name}."),
            Review(author="C. Shopper", rating=4, title="Solid", body="Works as described."),
        ]

    def product_pricing(product_id: str) -> PriceBreakdown:
        p = _require(category, product_id)
        discount = 20.0 if not p.in_stock else 10.0
        final = round(p.price * (1 - discount / 100), 2)
        return PriceBreakdown(
            product_id=p.id, list_price=p.price, discount_pct=discount, final_price=final
        )

    # --- write --------------------------------------------------------------
    def add_to_cart(product_id: str, body: CartRequest) -> CartLine:
        p = _require(category, product_id)
        return CartLine(
            product_id=p.id, name=p.name, quantity=body.quantity,
            line_total=round(p.price * body.quantity, 2),
        )

    # (path, method, fn, response_model, operation_id, summary)
    routes = [
        (f"{base}/products", "GET", list_products, list[Product],
         f"list_{category}_products", f"List all {label} products"),
        (f"{base}/featured", "GET", featured, list[Product],
         f"featured_{category}", f"Browse featured {label} products"),
        (f"{base}/bestsellers", "GET", bestsellers, list[Product],
         f"bestsellers_{category}", f"Top-selling {label} products"),
        (f"{base}/deals", "GET", deals, list[Deal],
         f"deals_{category}", f"Current {label} deals and discounts"),
        (f"{base}/inventory", "GET", inventory, list[Inventory],
         f"inventory_{category}", f"Stock levels for {label} products"),
        (f"{base}/products/{{product_id}}", "GET", product_detail, ProductDetail,
         f"detail_{category}_product", f"Get full details for a {label} product"),
        (f"{base}/products/{{product_id}}/specs", "GET", product_specs, dict,
         f"specs_{category}_product", f"Get the technical specifications of a {label} product"),
        (f"{base}/products/{{product_id}}/reviews", "GET", product_reviews, list[Review],
         f"reviews_{category}_product", f"Read customer reviews for a {label} product"),
        (f"{base}/products/{{product_id}}/pricing", "GET", product_pricing, PriceBreakdown,
         f"pricing_{category}_product", f"Get the price breakdown for a {label} product"),
        (f"{base}/products/{{product_id}}/cart", "POST", add_to_cart, CartLine,
         f"addcart_{category}_product", f"Add a {label} product to the shopping cart"),
    ]

    for path, method, fn, response_model, op_id, summary in routes:
        app.add_api_route(
            path, fn, methods=[method], response_model=response_model,
            operation_id=op_id, summary=summary, tags=[category],
        )


for _category in CATEGORIES:
    _register_category(_category)


# --- global endpoints --------------------------------------------------------

@app.get("/categories", operation_id="list_categories", summary="List all product categories",
         tags=["catalog"])
def list_categories() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in CATEGORIES.items()]


@app.get("/health", operation_id="health", summary="Service health check", tags=["catalog"])
def health() -> dict:
    return {"status": "ok", "categories": len(CATEGORIES), "base_url": BASE_URL}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("CATALOG_HOST", "0.0.0.0"), port=int(_PORT))


if __name__ == "__main__":
    main()
