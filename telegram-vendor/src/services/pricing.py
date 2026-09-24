"""Per-quantity pricing.

A product's price table is stored as JSON on ``Product.price_tiers``:
    [{"qty": 1, "total": 500}, {"qty": 2, "total": 900}, ...]

Each entry is the **total** price (JPY) for exactly that quantity, so every
quantity can be priced on its own instead of being locked to
``unit_price * quantity``. A quantity with no entry of its own is prorated
from the nearest lower entry (round half up to the yen).

Legacy rows ``[{"min": N, "price": P}]`` (where P was a unit price applying to
N and above) are read as ``{"qty": N, "total": N * P}``, which keeps the prices
of products configured before this change exactly as they were.

If a product has no table at all, its flat ``price`` is used as the 1-unit
total and every other quantity is a multiple of it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Tier:
    """The total price charged for exactly *quantity* units."""

    quantity: int
    total: int

    @property
    def unit_price(self) -> int:
        """Per-unit price, for display only (round half up)."""
        return _round_half_up(self.total, self.quantity)


def _round_half_up(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` rounded half up, in integer math."""
    if denominator <= 0:
        return numerator
    return (2 * numerator + denominator) // (2 * denominator)


def _prorate(quantity: int, tier: Tier) -> int:
    """Total for *quantity* derived from *tier*'s per-unit rate."""
    return _round_half_up(quantity * tier.total, tier.quantity)


def parse_tiers(raw: str | None, base_price: int) -> list[Tier]:
    """Return the sorted price table. Falls back to a single 1-unit entry."""
    tiers: list[Tier] = []
    if raw:
        try:
            data = json.loads(raw)
            for item in data:
                if "total" in item:
                    qty = int(item["qty"] if "qty" in item else item["min"])
                    tiers.append(Tier(qty, int(item["total"])))
                else:
                    qty = int(item["min"])
                    tiers.append(Tier(qty, qty * int(item["price"])))
        except (ValueError, KeyError, TypeError):
            tiers = []
    tiers = [t for t in tiers if t.quantity >= 1 and t.total >= 0]
    if not tiers:
        tiers = [Tier(1, base_price)]
    tiers.sort(key=lambda t: t.quantity)
    if tiers[0].quantity != 1:
        tiers.insert(0, Tier(1, _prorate(1, tiers[0])))
    return tiers


def total_for(quantity: int, tiers: list[Tier]) -> int:
    """Total charged for *quantity*.

    An entry for exactly that quantity wins; otherwise the nearest lower
    entry's per-unit rate is applied.
    """
    quantity = max(1, quantity)
    chosen = tiers[0]
    for t in tiers:
        if t.quantity == quantity:
            return t.total
        if t.quantity < quantity:
            chosen = t
        else:
            break
    return _prorate(quantity, chosen)


def unit_price_for(quantity: int, tiers: list[Tier]) -> int:
    """Per-unit price for *quantity*, for display (round half up)."""
    quantity = max(1, quantity)
    return _round_half_up(total_for(quantity, tiers), quantity)


def quantity_options(tiers: list[Tier]) -> list[int]:
    """Preset quantity buttons: the priced quantities (always includes 1)."""
    return sorted({t.quantity for t in tiers} | {1})


def tiers_to_json(tiers: list[Tier]) -> str:
    return json.dumps(
        [{"qty": t.quantity, "total": t.total} for t in tiers],
        ensure_ascii=False,
    )


def parse_tiers_text(text: str) -> str | None:
    """Parse admin input '数量:合計金額,数量:合計金額,...' into stored JSON.

    Example: "1:500,2:900,3:1300,10:4000" . Returns None if invalid.
    """
    tiers: list[Tier] = []
    seen: set[int] = set()
    for part in text.replace("、", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part and "：" not in part:
            return None
        sep = ":" if ":" in part else "："
        qty_s, total_s = part.split(sep, 1)
        try:
            qty, total = int(qty_s.strip()), int(total_s.strip())
        except ValueError:
            return None
        if qty < 1 or total < 0 or qty in seen:
            return None
        seen.add(qty)
        tiers.append(Tier(qty, total))
    if not tiers:
        return None
    tiers.sort(key=lambda t: t.quantity)
    return tiers_to_json(tiers)


def format_tiers(tiers: list[Tier]) -> str:
    """Human-readable price table (per-unit) for the product detail screen."""
    if len(tiers) == 1:
        return f"{tiers[0].unit_price:,}円/個"
    lines: list[str] = []
    for t in tiers:
        if t.quantity == 1:
            lines.append(f"  1個〜: {t.unit_price:,}円/個")
        else:
            lines.append(
                f"  {t.quantity}個〜: {t.unit_price:,}円/個"
                f"（{t.quantity}個 ¥{t.total:,}）"
            )
    return "\n".join(lines)
