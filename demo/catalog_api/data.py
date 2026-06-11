"""Categories, response models, and deterministic seeded product data.

Everything here is generated from a fixed seed so the demo returns stable data across
runs (no database, no faker dependency) — point SpecRouter at the served spec and the
same product ids/details come back every time.
"""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel, Field

# --- categories --------------------------------------------------------------
# (key used in URLs/operation_ids, human label, short code for product ids, a pool of
# product nouns, and a pool of attribute adjectives that flavour the seeded data.)
CATEGORIES: dict[str, dict[str, Any]] = {
    "electronics": {
        "label": "Electronics",
        "code": "elec",
        "nouns": ["Laptop", "Smartphone", "Headphones", "4K Monitor", "Mechanical Keyboard",
                  "Wireless Mouse", "Tablet", "Smartwatch"],
        "brands": ["Volt", "Nimbus", "Cobalt", "Apex"],
    },
    "books": {
        "label": "Books",
        "code": "book",
        "nouns": ["Hardcover Novel", "Cookbook", "Sci-Fi Paperback", "Biography",
                  "Children's Picture Book", "History Anthology", "Poetry Collection"],
        "brands": ["Harbor Press", "Quill House", "Lantern Books"],
    },
    "clothing": {
        "label": "Clothing",
        "code": "cloth",
        "nouns": ["Cotton T-Shirt", "Denim Jacket", "Running Shorts", "Wool Sweater",
                  "Rain Coat", "Linen Shirt", "Hoodie"],
        "brands": ["Northwind", "Stitch&Co", "Meridian"],
    },
    "home_kitchen": {
        "label": "Home & Kitchen",
        "code": "home",
        "nouns": ["Espresso Machine", "Cast-Iron Skillet", "Chef's Knife", "Blender",
                  "Air Fryer", "Dinnerware Set", "Stand Mixer"],
        "brands": ["Hearth", "CopperLane", "Everwarm"],
    },
    "toys": {
        "label": "Toys",
        "code": "toy",
        "nouns": ["Building Block Set", "Plush Bear", "Remote Car", "Puzzle 1000pc",
                  "Board Game", "Art Kit", "Action Figure"],
        "brands": ["PlayLab", "BrightSpark", "TinkerTown"],
    },
    "sports": {
        "label": "Sports & Outdoors",
        "code": "sport",
        "nouns": ["Yoga Mat", "Dumbbell Set", "Mountain Bike Helmet", "Tennis Racket",
                  "Camping Tent", "Running Shoes", "Water Bottle"],
        "brands": ["Summit", "PaceMaker", "TrailHead"],
    },
    "beauty": {
        "label": "Beauty",
        "code": "beauty",
        "nouns": ["Vitamin-C Serum", "Matte Lipstick", "Hydrating Cream", "Hair Dryer",
                  "Eyeshadow Palette", "Cleansing Gel", "Sunscreen SPF50"],
        "brands": ["Lumière", "Petal", "GlowLab"],
    },
    "grocery": {
        "label": "Grocery",
        "code": "groc",
        "nouns": ["Single-Origin Coffee", "Olive Oil", "Dark Chocolate", "Granola",
                  "Pasta Sampler", "Hot Sauce", "Green Tea"],
        "brands": ["Field&Vine", "Pantry Co", "Harvest"],
    },
    "automotive": {
        "label": "Automotive",
        "code": "auto",
        "nouns": ["Dash Cam", "Tire Inflator", "Car Vacuum", "Jump Starter",
                  "Phone Mount", "Wiper Blades", "Seat Cover"],
        "brands": ["RoadOne", "Torque", "AutoPro"],
    },
    "garden": {
        "label": "Garden",
        "code": "gard",
        "nouns": ["Pruning Shears", "Garden Hose", "Raised Bed Kit", "Watering Can",
                  "Solar Lantern", "Potting Soil 20L", "Hedge Trimmer"],
        "brands": ["GreenThumb", "Sprout", "Verdant"],
    },
}

_CURRENCY = "USD"


# --- response models ---------------------------------------------------------

class Product(BaseModel):
    id: str = Field(..., description="Stable product identifier, e.g. 'elec-001'.")
    name: str = Field(..., description="Display name of the product.")
    category: str = Field(..., description="Category key the product belongs to.")
    brand: str
    price: float = Field(..., description="Unit price.")
    currency: str = _CURRENCY
    rating: float = Field(..., ge=0, le=5, description="Average customer rating (0-5).")
    in_stock: bool


class ProductDetail(Product):
    description: str
    attributes: dict[str, str] = Field(default_factory=dict, description="Key spec attributes.")
    image_urls: list[str] = Field(default_factory=list)


class Review(BaseModel):
    author: str
    rating: int = Field(..., ge=1, le=5)
    title: str
    body: str


class PriceBreakdown(BaseModel):
    product_id: str
    list_price: float
    discount_pct: float = Field(..., description="Active discount percentage.")
    final_price: float
    currency: str = _CURRENCY


class Deal(BaseModel):
    product_id: str
    name: str
    was: float
    now: float
    ends_in_days: int


class Inventory(BaseModel):
    product_id: str
    warehouse: str
    quantity: int


class CartLine(BaseModel):
    product_id: str
    name: str
    quantity: int
    line_total: float
    currency: str = _CURRENCY


class CartRequest(BaseModel):
    quantity: int = Field(1, ge=1, le=100, description="How many units to add to the cart.")


# --- deterministic seeded catalog -------------------------------------------

def _rng(category: str) -> random.Random:
    """A stable per-category RNG so data is identical across runs/processes."""
    return random.Random(f"acme::{category}")


def _build_products(category: str) -> list[ProductDetail]:
    meta = CATEGORIES[category]
    rng = _rng(category)
    code = meta["code"]
    products: list[ProductDetail] = []
    for i, noun in enumerate(meta["nouns"], start=1):
        brand = rng.choice(meta["brands"])
        price = round(rng.uniform(9.99, 899.0), 2)
        products.append(
            ProductDetail(
                id=f"{code}-{i:03d}",
                name=f"{brand} {noun}",
                category=category,
                brand=brand,
                price=price,
                rating=round(rng.uniform(3.2, 5.0), 1),
                in_stock=rng.random() > 0.15,
                description=f"The {brand} {noun} — a popular pick in {meta['label']}.",
                attributes={
                    "color": rng.choice(["Black", "White", "Blue", "Silver", "Natural"]),
                    "weight_kg": str(round(rng.uniform(0.1, 6.0), 2)),
                    "warranty_months": str(rng.choice([6, 12, 24, 36])),
                },
                image_urls=[f"https://cdn.example.com/{code}/{i:03d}/main.jpg"],
            )
        )
    return products


# Built once at import time; deterministic, so safe to reuse across requests.
CATALOG: dict[str, list[ProductDetail]] = {c: _build_products(c) for c in CATEGORIES}


def get_product(category: str, product_id: str) -> ProductDetail | None:
    return next((p for p in CATALOG[category] if p.id == product_id), None)
