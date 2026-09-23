"""Tiered (bulk-discount) pricing helpers.

A product's price tiers are stored as a JSON list on ``Product.price_tiers``:
    [{"min": 1, "price": 1800}, {"min": 5, "price": 1600}, ...]

If a product has no tiers, its flat ``price`` is used for every quantity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Tier:
    min_qty: int
    unit_price: int


def parse_tiers(raw: str | None, base_price: int) -> list[Tier]:
    """Return sorted tiers. Falls back to a single tier at *base_price*."""
    tiers: list[Tier] = []
    if raw:
        try:
            data = json.loads(raw)
            for item in data:
                tiers.append(Tier(int(item["min"]), int(item["price"])))
        except (ValueError, KeyError, TypeError):
            tiers = []
    if not tiers:
        tiers = [Tier(1, base_price)]
    tiers.sort(key=lambda t: t.min_qty)
    if tiers[0].min_qty != 1:
        # Ensure quantity 1 always has a price (use the lowest tier's).
        tiers.insert(0, Tier(1, tiers[0].unit_price))
    return tiers


def unit_price_for(quantity: int, tiers: list[Tier]) -> int:
    """Unit price applied for *quantity* (highest tier whose min_qty <= qty)."""
    price = tiers[0].unit_price
    for t in tiers:
        if quantity >= t.min_qty:
            price = t.unit_price
        else:
            break
    return price


def total_for(quantity: int, tiers: list[Tier]) -> int:
    return quantity * unit_price_for(quantity, tiers)


def quantity_options(tiers: list[Tier]) -> list[int]:
    """Preset quantity buttons: the tier thresholds (always includes 1)."""
    opts = sorted({t.min_qty for t in tiers} | {1})
    return opts


def tiers_to_json(tiers: list[Tier]) -> str:
    return json.dumps(
        [{"min": t.min_qty, "price": t.unit_price} for t in tiers],
        ensure_ascii=False,
    )


def parse_tiers_text(text: str) -> str | None:
    """Parse admin input 'min:price,min:price,...' into stored JSON.

    Example: "1:1800,5:1600,10:1500,50:1000" . Returns None if invalid.
    """
    tiers: list[Tier] = []
    for part in text.replace("、", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part and "：" not in part:
            return None
        sep = ":" if ":" in part else "："
        min_s, price_s = part.split(sep, 1)
        try:
            tiers.append(Tier(int(min_s.strip()), int(price_s.strip())))
        except ValueError:
            return None
    if not tiers:
        return None
    tiers.sort(key=lambda t: t.min_qty)
    return tiers_to_json(tiers)


def format_tiers(tiers: list[Tier]) -> str:
    """Human-readable price table for the product detail screen."""
    lines: list[str] = []
    for i, t in enumerate(tiers):
        if i == 0:
            lines.append(f"  通常: {t.unit_price:,}円")
        else:
            lines.append(f"  {t.min_qty}個以上: {t.unit_price:,}円")
    return "\n".join(lines)
