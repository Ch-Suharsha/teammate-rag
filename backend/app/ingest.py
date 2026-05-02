"""Ingest the Amazon Products 2023 dataset into Qdrant.

Run inside the API container (`compose` mounts `./final version/data` at `/data`):

    docker compose run --rm --build api python -m app.ingest --batch-size 256

Defaults read `/data/amazon_products_final.csv` and `/data/amazon_categories.csv`
(no CLI paths — avoids Git Bash rewriting `/data/...` into `C:/Program Files/Git/...`).

Optional overrides: `--products` / `--categories` (use `//data/…` prefix in Git Bash
if you pass paths manually, or `MSYS_NO_PATHCONV=1`).

Features:
- Idempotent upsert keyed by ASIN (rerun-safe).
- Checkpoint file records the last successfully committed row offset.
- Normalises model parity: ingest model + dim must match runtime env.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from qdrant_client.http import models as qm

from .embedder import embed_texts
from .rag import ensure_collection, get_qdrant
from .settings import get_settings

log = logging.getLogger("ingest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _asin_to_uuid(asin: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"amazon-asin/{asin}"))


def _load_categories(path: Path) -> Dict[str, str]:
    if not path.exists():
        log.warning("Category file %s missing; products will have no category names", path)
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["id"]: row["category_name"] for row in reader if row.get("id")}


def _build_text(row: Dict[str, str], categories: Dict[str, str]) -> str:
    parts = [row.get("title", "")]
    cat = categories.get(row.get("category_id", ""), "")
    if cat:
        parts.append(f"Category: {cat}")
    if row.get("price"):
        parts.append(f"Price: ${row['price']}")
    if row.get("stars"):
        parts.append(f"Rating: {row['stars']} stars")
    return ". ".join(p for p in parts if p)


def _read_checkpoint(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except ValueError:
        return 0


def _write_checkpoint(path: Path, offset: int) -> None:
    path.write_text(str(offset))


def _iter_rows(path: Path, skip: int) -> Iterable[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader):
            if idx < skip:
                continue
            yield row


def ingest(
    products_csv: Path,
    categories_csv: Path,
    batch_size: int = 256,
    limit: int = 0,
    checkpoint: Optional[Path] = None,
) -> None:
    settings = get_settings()
    ensure_collection()
    client = get_qdrant()
    categories = _load_categories(categories_csv)

    checkpoint = checkpoint or Path("/tmp/ingest_amazon.ckpt")
    skip = _read_checkpoint(checkpoint)
    if skip:
        log.info("Resuming from checkpoint offset %d", skip)

    buf_rows: List[Dict[str, str]] = []
    buf_texts: List[str] = []
    processed = skip
    submitted = 0

    def flush() -> None:
        nonlocal processed, submitted, buf_rows, buf_texts
        if not buf_texts:
            return
        vectors = embed_texts(buf_texts)
        points: List[qm.PointStruct] = []
        for row, vec in zip(buf_rows, vectors):
            asin = row.get("asin")
            if not asin:
                continue
            payload = {
                "asin": asin,
                "title": row.get("title", ""),
                "category": categories.get(row.get("category_id", "")),
                "price": float(row["price"]) if row.get("price") else None,
                "stars": float(row["stars"]) if row.get("stars") else None,
                "url": row.get("productURL"),
            }
            points.append(
                qm.PointStruct(id=_asin_to_uuid(asin), vector=vec.tolist(), payload=payload)
            )
        if points:
            client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
            submitted += len(points)
        processed += len(buf_rows)
        _write_checkpoint(checkpoint, processed)
        log.info("Processed %d rows (submitted %d total this run)", processed, submitted)
        buf_rows.clear()
        buf_texts.clear()

    for row in _iter_rows(products_csv, skip):
        buf_rows.append(row)
        buf_texts.append(_build_text(row, categories))
        if len(buf_texts) >= batch_size:
            flush()
            if limit and submitted >= limit:
                break

    flush()
    log.info("Done. Total upserted this run: %d", submitted)


def _default_products() -> Path:
    return Path("/data/amazon_products_final.csv")


def _default_categories() -> Path:
    return Path("/data/amazon_categories.csv")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--products",
        type=Path,
        default=_default_products(),
        help="CSV path inside the container (default: /data/amazon_products_final.csv)",
    )
    parser.add_argument(
        "--categories",
        type=Path,
        default=_default_categories(),
        help="CSV path inside the container (default: /data/amazon_categories.csv)",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--reset-checkpoint", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.reset_checkpoint and args.checkpoint and args.checkpoint.exists():
        args.checkpoint.unlink()
    ingest(
        products_csv=args.products,
        categories_csv=args.categories,
        batch_size=args.batch_size,
        limit=args.limit,
        checkpoint=args.checkpoint,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
