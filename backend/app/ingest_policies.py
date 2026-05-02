"""Ingest the support policy KB into a dedicated Qdrant collection.

Run:
    docker compose run --rm --build api python -m app.ingest_policies

Defaults to ``/data/policies.csv`` inside the container. Idempotent: identical
``topic + section + source`` produces a stable UUID so reruns upsert in place.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from qdrant_client.http import models as qm

from .embedder import embed_texts
from .rag import ensure_policy_collection, get_qdrant
from .settings import get_settings

log = logging.getLogger("ingest_policies")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _stable_id(topic: str, section: str, source: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"atlas-policy/{topic}/{section}/{source}"))


def _load_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        log.error("Policy CSV not found at %s", path)
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in reader]
    return [r for r in rows if r.get("text")]


def _build_text(row: Dict[str, str]) -> str:
    parts = [row.get("topic", ""), row.get("section", ""), row.get("text", "")]
    return ". ".join(p for p in parts if p)


def ingest(csv_path: Path) -> int:
    settings = get_settings()
    ensure_policy_collection()
    client = get_qdrant()

    rows = _load_rows(csv_path)
    if not rows:
        log.warning("No policy rows to ingest from %s", csv_path)
        return 0

    texts = [_build_text(r) for r in rows]
    vectors = embed_texts(texts)

    points: List[qm.PointStruct] = []
    for row, vec in zip(rows, vectors):
        topic = row.get("topic", "")
        section = row.get("section", "")
        source = row.get("source", "")
        if not (topic and row.get("text")):
            continue
        payload = {
            "topic": topic,
            "section": section,
            "source": source,
            "text": row.get("text", ""),
        }
        points.append(
            qm.PointStruct(
                id=_stable_id(topic, section, source),
                vector=vec.tolist(),
                payload=payload,
            )
        )
    client.upsert(collection_name=settings.qdrant_policy_collection, points=points, wait=True)
    log.info(
        "Upserted %d policy points into %s",
        len(points),
        settings.qdrant_policy_collection,
    )
    return len(points)


def _default_path() -> Path:
    return Path("/data/policies.csv")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=_default_path())
    args = parser.parse_args(argv or sys.argv[1:])
    ingest(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
