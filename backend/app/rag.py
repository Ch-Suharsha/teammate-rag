from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .embedder import embed_query
from .settings import get_settings

log = logging.getLogger(__name__)

_client: Optional[QdrantClient] = None


def get_qdrant() -> QdrantClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = QdrantClient(url=settings.qdrant_url, timeout=30)
    return _client


def _ensure_named_collection(name: str) -> None:
    settings = get_settings()
    client = get_qdrant()
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        return
    client.create_collection(
        collection_name=name,
        vectors_config=qm.VectorParams(
            size=settings.embedding_dim, distance=qm.Distance.COSINE
        ),
    )
    log.info("Created Qdrant collection %s", name)


def ensure_collection() -> None:
    """Catalog collection (the one ingest.py fills)."""
    _ensure_named_collection(get_settings().qdrant_collection)


def ensure_policy_collection() -> None:
    _ensure_named_collection(get_settings().qdrant_policy_collection)


@dataclass
class RagHit:
    asin: str
    title: str
    score: float
    category: Optional[str] = None
    price: Optional[float] = None
    url: Optional[str] = None
    stars: Optional[float] = None


@dataclass
class PolicyHit:
    topic: str
    text: str
    score: float
    source: Optional[str] = None
    section: Optional[str] = None


def _query(collection: str, vector: List[float], top_k: int):
    client = get_qdrant()
    try:
        resp = client.query_points(
            collection_name=collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        return resp.points
    except Exception as exc:
        log.exception("Qdrant search failed for collection=%s", collection)
        raise RuntimeError(f"qdrant search failed: {exc}") from exc


def _embed_or_raise(query: str) -> List[float]:
    try:
        return embed_query(query)
    except Exception as exc:
        log.exception("Embedder failed for query=%r", query)
        raise RuntimeError(f"embedding failed: {exc}") from exc


def search_products(query: str, top_k: int = 6) -> List[RagHit]:
    settings = get_settings()
    vector = _embed_or_raise(query)
    points = _query(settings.qdrant_collection, vector, top_k)

    hits: List[RagHit] = []
    for p in points:
        payload = p.payload or {}
        hits.append(
            RagHit(
                asin=str(payload.get("asin", "")),
                title=str(payload.get("title", "")),
                score=float(p.score) if p.score is not None else 0.0,
                category=payload.get("category"),
                price=payload.get("price"),
                url=payload.get("url"),
                stars=payload.get("stars"),
            )
        )
    return hits


def search_policies(query: str, top_k: int = 4) -> List[PolicyHit]:
    settings = get_settings()
    vector = _embed_or_raise(query)
    try:
        points = _query(settings.qdrant_policy_collection, vector, top_k)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "doesn" in msg and "exist" in msg or "404" in msg:
            return []
        raise

    hits: List[PolicyHit] = []
    for p in points:
        payload = p.payload or {}
        hits.append(
            PolicyHit(
                topic=str(payload.get("topic", "")),
                text=str(payload.get("text", "")),
                source=payload.get("source"),
                section=payload.get("section"),
                score=float(p.score) if p.score is not None else 0.0,
            )
        )
    return hits


def _stats_for(collection: str) -> dict:
    client = get_qdrant()
    try:
        info = client.get_collection(collection)
        return {
            "name": collection,
            "points": int(info.points_count or 0),
            "vectors": int(info.vectors_count or info.points_count or 0),
            "status": str(info.status) if info.status is not None else "unknown",
        }
    except Exception as exc:
        log.warning("Qdrant stats unavailable for %s: %s", collection, exc)
        return {"name": collection, "points": 0, "status": "unavailable", "error": str(exc)}


def collection_stats() -> dict:
    """Lightweight stats for the UI catalog explorer (works during ingestion)."""
    settings = get_settings()
    out = _stats_for(settings.qdrant_collection)
    out["policy"] = _stats_for(settings.qdrant_policy_collection)
    return out
