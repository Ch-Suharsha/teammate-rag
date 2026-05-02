"""Seed Postgres with one demo customer (id ``1``) and 120 orders.

Run:
    docker compose run --rm api python -m app.seed

Idempotent: skips customers/orders that already exist by primary key.

For a clean slate (only user ``1``), reset the DB volume or truncate tables, then re-run seed.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Dict, List

from .db import session_scope, engine
from .models import Base, Customer, Order

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CUSTOMER_ID = "1"

CUSTOMER_ROW: Dict[str, Any] = {
    "id": CUSTOMER_ID,
    "name": "Demo Customer",
    "email": "demo@atlas.local",
    "tier": "Gold",
}

# Deterministic-ish variety (reproducible across machines)
RNG = random.Random(20260502)

_LINE_ITEMS = (
    ("USB-C hub", "Wireless earbuds", "Phone case", "Laptop sleeve", "HDMI cable"),
    ("Coffee beans 1kg", "Travel mug", "Desk lamp", "Webcam HD", "Keyboard mechanical"),
    ("Monitor stand", "SSD 1TB", "USB flash drive", "Surge protector", "Mouse pad XL"),
    ("Fitness tracker", "Yoga mat", "Water bottle insulated", "Backpack commuter", "Sunglasses"),
    ("Garden hose", "LED strip lights", "Tool kit", "Phone charger brick", "Screen wipes"),
)


def _synthetic_orders(customer_id: str, count_after_flagship: int) -> List[Dict[str, Any]]:
    statuses = ("Delivered", "Shipped", "Out for Delivery", "Processing", "Cancelled")
    carriers = ("UPS", "FedEx", "USPS", "DHL", "UPS")
    out: List[Dict[str, Any]] = []

    flagship: Dict[str, Any] = {
        "id": "ORD-88210",
        "customer_id": customer_id,
        "status": "Shipped",
        "carrier": "UPS",
        "tracking": "1Z999AA10123456784",
        "eta": "2026-05-08",
        "items": [{"title": "Blue Wireless Headphones", "qty": 1}],
        "total_cents": 8999,
    }
    out.append(flagship)

    for seq in range(1, count_after_flagship + 1):
        st = statuses[seq % len(statuses)]
        if st in {"Processing", "Cancelled"}:
            carrier = tracking = eta = None
        else:
            carrier = carriers[seq % len(carriers)]
            digits = f"{(8800000000000 + seq * 991) % 10**12:012d}"
            tracking = f"1Z{digits}US" if carrier == "UPS" else f"{digits}-TRK-{seq}"
            eta = f"2026-{(seq % 7) + 5:02d}-{(seq % 26) + 1:02d}"

        group = _LINE_ITEMS[seq % len(_LINE_ITEMS)]
        n_items = RNG.randint(1, min(4, len(group)))
        pics = RNG.sample(group, k=n_items)
        items = [{"title": p, "qty": RNG.randint(1, 3)} for p in pics]
        qty_sum = sum(x["qty"] for x in items)
        unit = RNG.randint(800, 15000)
        total_cents = min(599_999, max(599, qty_sum * unit // max(1, len(items))))

        out.append(
            {
                "id": f"ORD-GEN-{seq:05d}",
                "customer_id": customer_id,
                "status": st,
                "carrier": carrier,
                "tracking": tracking,
                "eta": eta,
                "items": items,
                "total_cents": total_cents,
            }
        )
    return out


# 1 flagship + 119 generated = 120 orders (>= 100)
ORDERS = _synthetic_orders(CUSTOMER_ID, count_after_flagship=119)


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        if db.get(Customer, CUSTOMER_ID) is None:
            db.add(
                Customer(
                    id=CUSTOMER_ROW["id"],
                    name=CUSTOMER_ROW["name"],
                    email=CUSTOMER_ROW["email"],
                    tier=CUSTOMER_ROW["tier"],
                    member_since=datetime.utcnow(),
                )
            )
            log.info("Inserted customer id=%s", CUSTOMER_ID)
        else:
            log.info("Customer id=%s already present; skipping insert", CUSTOMER_ID)

        created = skipped = 0
        for o in ORDERS:
            if db.get(Order, o["id"]) is not None:
                skipped += 1
                continue
            db.add(Order(**o))
            created += 1

    log.info(
        "Seeded orders:+%d skipped(existing):%d (customer=%s)",
        created,
        skipped,
        CUSTOMER_ID,
    )


if __name__ == "__main__":
    seed()
