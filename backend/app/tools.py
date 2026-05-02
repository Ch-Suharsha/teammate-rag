"""Tool definitions and live executors backed by Postgres + Qdrant.

These are the only side-effect surface the LLM is allowed to touch.
Each handler validates inputs, queries real data, and returns a JSON-serializable dict.
"""
from __future__ import annotations

import hashlib
import logging
import re
import random
import string
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import rag
from .mailer import send_email
from .models import Customer, Order, Refund, SupportTicket
from .settings import get_settings

log = logging.getLogger(__name__)


def _format_listing_lines(items: List[Dict[str, Any]], cap: int = 2) -> List[str]:
    """Render related-product dicts into bullet-friendly strings for emails / replies."""
    lines: List[str] = []
    for it in (items or [])[:cap]:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        meta_bits: List[str] = []
        if it.get("category"):
            meta_bits.append(str(it["category"]))
        if it.get("price") is not None:
            try:
                meta_bits.append(f"${float(it['price']):.2f}")
            except (TypeError, ValueError):
                pass
        if it.get("asin"):
            meta_bits.append(f"ASIN {it['asin']}")
        meta = (" · " + " · ".join(meta_bits)) if meta_bits else ""
        lines.append(f"- {title}{meta}")
    return lines


def _order_item_blob(items: Optional[List[Any]]) -> str:
    parts: List[str] = []
    for it in items or []:
        if isinstance(it, dict):
            t = str(it.get("title") or it.get("name") or "").strip()
            if t:
                parts.append(t)
        elif it is not None:
            parts.append(str(it))
    return " ".join(parts).lower()


def _search_tokens(q: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 1]


def _did_you_mean(order_id: str, ctx: "ToolContext") -> List[Dict[str, Any]]:
    """When an order id misses, suggest sibling orders for the authenticated customer."""
    customer = _resolve_customer(ctx.db, ctx)
    if not customer:
        return []
    rows = (
        ctx.db.query(Order)
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .limit(20)
        .all()
    )
    if not rows:
        return []
    needle = order_id.replace("ORD-", "").replace("-", "").upper()
    scored: List[tuple] = []
    for r in rows:
        flat = r.id.replace("ORD-", "").replace("-", "").upper()
        score = 0
        if needle and (needle in flat or flat in needle):
            score += 5
        common = sum(1 for ch in needle if ch in flat)
        score += min(common, 6)
        scored.append((score, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, r in scored[:3]:
        out.append({"order_id": r.id, "status": r.status, "eta": r.eta or "TBD", "match_score": score})
    return out


# ── Side-channel email (fired by deterministic tools, not the LLM) ─
def _auto_email(
    ctx: "ToolContext",
    *,
    subject: str,
    body: str,
    label: str,
) -> Dict[str, Any]:
    """Send a transactional email and return a tiny status dict.

    The LLM doesn't decide whether to send confirmations — the tool itself does,
    so a refund or ticket always produces an audit-ready Mailhog message.
    """
    customer = _resolve_customer(ctx.db, ctx)
    to_address = customer.email if customer else ctx.customer_email
    if not to_address:
        return {"sent": False, "skipped": "no_customer_email"}
    try:
        record = send_email(
            ctx.db,
            to_address=to_address,
            subject=subject,
            body=body,
            session_id=ctx.session_id,
        )
        log.info("Auto-email[%s] -> %s status=%s", label, to_address, record.status)
        return {
            "sent": record.status in {"sent", "queued"},
            "status": record.status,
            "to": to_address,
            "email_id": record.id,
        }
    except Exception as exc:
        log.exception("Auto-email[%s] failed", label)
        return {"sent": False, "error": str(exc)}


def _meaningful_tokens(text: str, min_len: int) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= min_len]


def _title_matches_any_token(query: str, title: str, min_token_len: int) -> bool:
    toks = _meaningful_tokens(query, min_token_len)
    if not toks:
        return True
    tl = (title or "").lower()
    return any(tok in tl for tok in toks)


def _filter_catalog_hits(
    hits: List[rag.RagHit],
    *,
    query_for_overlap: str,
    take: int,
    min_score: float,
    require_keyword_overlap: bool,
    keyword_min_len: int,
    strong_semantic_score: float,
) -> List[rag.RagHit]:
    out: List[rag.RagHit] = []
    for h in hits:
        score = float(h.score) if h.score is not None else 0.0
        if score < min_score:
            continue
        if require_keyword_overlap and not _title_matches_any_token(
            query_for_overlap, h.title or "", keyword_min_len
        ):
            if score < strong_semantic_score:
                continue
        out.append(h)
        if len(out) >= take:
            break
    return out


def _related_products(query: str, top_k: int, ctx: "ToolContext") -> List[Dict[str, Any]]:
    """Catalog neighbors for order lines / escalation — gated by score + token overlap."""
    if not query:
        return []
    settings = get_settings()
    # Over-fetch then filter — partial indexes often return mediocre top-2 otherwise.
    scan_k = min(max(top_k * 5, 20), 48)
    rag_query = f"product matching or similar to: {query}"
    try:
        hits = rag.search_products(query=rag_query, top_k=scan_k)
    except Exception as exc:
        log.warning("Related-product RAG failed: %s", exc)
        return []
    filtered = _filter_catalog_hits(
        hits,
        query_for_overlap=query,
        take=top_k,
        min_score=settings.rag_related_min_score,
        require_keyword_overlap=settings.rag_related_require_keyword_overlap,
        keyword_min_len=settings.rag_related_keyword_min_len,
        strong_semantic_score=settings.rag_related_strong_semantic_score,
    )
    if not filtered:
        return []
    ctx.rag_hits.extend(filtered)
    return [
        {
            "asin": h.asin,
            "title": h.title,
            "category": h.category,
            "price": h.price,
            "stars": h.stars,
            "score": round(float(h.score) if h.score is not None else 0.0, 4),
        }
        for h in filtered
    ]


# ── OpenAI tool schemas ────────────────────────────────────────────
TOOL_SCHEMAS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": (
                "Look up an order by its exact ID for the authenticated customer only when the "
                "user provided that ID or you picked it from search_customer_orders. Do not invent IDs. "
                "Returns status, carrier, tracking, ETA, items, total, optional related_products."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order identifier such as ORD-88210"}
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_customer_orders",
            "description": "List or search THIS customer's orders in Postgres by optional keywords matched against order ID and line-item titles/details. Use BEFORE lookup_order whenever the customer asks whether they ordered something ('did I buy headphones?', 'anything with USB?') or gives no order_id. Omit keywords to fetch the latest orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "Free text; matched as word tokens against order id + item titles (e.g. 'headphone', 'wireless hub')",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_refund",
            "description": "Initiate a refund for an order belonging to the authenticated customer. Idempotent: repeated calls with the same reason for the same order return the existing refund.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "reason": {"type": "string", "description": "Why the customer wants a refund"},
                },
                "required": ["order_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_info",
            "description": "Fetch the authenticated customer's profile, tier, and order summary.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_product_knowledge",
            "description": "Search the Amazon product catalog by free-text query. Returns top matches with title, category, price, and rating. Use for product questions and recommendations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policy_knowledge",
            "description": "Search the Atlas Support policy knowledge base (refunds, returns, shipping, warranty, account). Always call this before answering any policy/eligibility/timeline question instead of guessing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Open a support ticket so a human agent takes over. Use for complaints, repeated frustration, or anything outside policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "default": "medium",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_customer_email",
            "description": "Send an email to the authenticated customer's address (e.g. refund confirmation, ticket summary).",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"],
                "additionalProperties": False,
            },
        },
    },
]


# ── Helpers ────────────────────────────────────────────────────────
def _format_money(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:.2f}"


def _request_key(reason: str) -> str:
    return hashlib.sha1(reason.strip().lower().encode()).hexdigest()[:16]


def _resolve_customer(db: Session, ctx: "ToolContext") -> Optional[Customer]:
    if ctx.customer_id:
        c = db.get(Customer, ctx.customer_id)
        if c is not None:
            return c
    if ctx.customer_email:
        return db.query(Customer).filter(Customer.email == ctx.customer_email).one_or_none()
    return None


# ── Context the agent injects ──────────────────────────────────────
class ToolContext:
    """Per-request data passed to every tool. Never user-visible."""

    def __init__(
        self,
        db: Session,
        session_id: str,
        customer_id: Optional[str],
        customer_email: Optional[str],
    ):
        self.db = db
        self.session_id = session_id
        self.customer_id = customer_id
        self.customer_email = customer_email
        self.rag_hits: List[rag.RagHit] = []


# ── Executors ──────────────────────────────────────────────────────
def tool_lookup_order(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    order_id = str(args.get("order_id", "")).strip().upper()
    if not order_id:
        return {"ok": False, "error": "order_id required"}
    order = ctx.db.get(Order, order_id)
    if not order:
        suggestions = _did_you_mean(order_id, ctx)
        return {
            "ok": False,
            "found": False,
            "message": f"Order {order_id} not found.",
            "did_you_mean": suggestions,
        }

    customer = _resolve_customer(ctx.db, ctx)
    if customer and order.customer_id != customer.id:
        return {
            "ok": False,
            "error": (
                "This order does not belong to the authenticated customer. "
                "Use search_customer_orders to find orders for this login, "
                "or verify Customer ID/email matches the seeded account."
            ),
        }

    items = order.items or []
    item_titles = [
        str(it.get("title") or it.get("name") or "").strip()
        for it in items
        if isinstance(it, dict)
    ]
    cross_sell = _related_products(
        query=" ".join(t for t in item_titles if t)[:280],
        top_k=3,
        ctx=ctx,
    )

    return {
        "ok": True,
        "found": True,
        "order_id": order.id,
        "status": order.status,
        "carrier": order.carrier or "Not yet assigned",
        "tracking": order.tracking or "Not yet available",
        "eta": order.eta or "TBD",
        "items": items,
        "total": _format_money(order.total_cents, order.currency),
        "related_products": cross_sell,
    }


def tool_search_customer_orders(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    customer = _resolve_customer(ctx.db, ctx)
    if not customer:
        return {
            "ok": False,
            "error": "No authenticated customer. Set Customer ID / Email in the console.",
        }

    keywords = str(args.get("keywords", "") or "").strip()
    limit = max(1, min(int(args.get("limit", 20)), 50))
    tokens = _search_tokens(keywords)

    rows = (
        ctx.db.query(Order)
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .all()
    )

    summaries: List[Dict[str, Any]] = []
    for o in rows:
        blob = _order_item_blob(o.items) + " " + o.id.lower()
        if tokens:
            if not any(tok in blob or tok in o.id.lower() for tok in tokens):
                continue
        item_preview = []
        for it in (o.items or [])[:4]:
            if isinstance(it, dict):
                lbl = str(it.get("title") or it.get("name") or "").strip()
                if not lbl:
                    continue
                try:
                    qn = int(it.get("qty", 1))
                except (TypeError, ValueError):
                    qn = 1
                item_preview.append(f"{lbl} x{qn}")
            elif it is not None:
                item_preview.append(str(it))
        summaries.append(
            {
                "order_id": o.id,
                "status": o.status,
                "eta": o.eta or "TBD",
                "total": _format_money(o.total_cents, o.currency),
                "items_preview": item_preview,
            }
        )
        if len(summaries) >= limit:
            break

    return {
        "ok": True,
        "count": len(summaries),
        "customer_id": customer.id,
        "orders": summaries,
        "message": (
            f"Found {len(summaries)} order(s) matching your search."
            if tokens
            else f"Latest {len(summaries)} order(s) for this customer."
        ),
    }


def tool_process_refund(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    order_id = str(args.get("order_id", "")).strip().upper()
    reason = str(args.get("reason", "")).strip() or "Customer request"
    if not order_id:
        return {"ok": False, "error": "order_id required"}

    order = ctx.db.get(Order, order_id)
    if not order:
        return {"ok": False, "message": f"Order {order_id} not found."}

    customer = _resolve_customer(ctx.db, ctx)
    if customer and order.customer_id != customer.id:
        return {"ok": False, "error": "Order does not belong to authenticated customer."}

    if order.status.lower() == "processing":
        return {
            "ok": False,
            "message": "Order is still processing. Please cancel instead, or wait until it ships.",
        }

    key = _request_key(reason)
    existing = (
        ctx.db.query(Refund)
        .filter(Refund.order_id == order.id, Refund.request_key == key)
        .one_or_none()
    )
    items = order.items or []
    item_titles = [
        str(it.get("title") or it.get("name") or "").strip()
        for it in items
        if isinstance(it, dict)
    ]
    listing_query = " ".join(t for t in item_titles if t)[:280]
    related = _related_products(query=listing_query, top_k=2, ctx=ctx) if listing_query else []
    listing_lines = _format_listing_lines(related, cap=2)
    listing_block = ("\n\nReference listing(s):\n" + "\n".join(listing_lines)) if listing_lines else ""

    if existing:
        amount_str = _format_money(existing.amount_cents, existing.currency)
        email = _auto_email(
            ctx,
            subject=f"Refund #{existing.id} status · order {order.id}",
            body=(
                f"Hi,\n\nWe already have refund #{existing.id} on file for order {order.id}.\n"
                f"Reason: {reason}\nAmount: {amount_str}\nCurrent status: {existing.status}."
                f"{listing_block}\n\n"
                "If something looks wrong, just reply to this thread and a teammate will jump in.\n\n— Atlas Support"
            ),
            label="refund_idempotent",
        )
        return {
            "ok": True,
            "idempotent": True,
            "refund_id": existing.id,
            "status": existing.status,
            "amount": amount_str,
            "message": "Existing refund returned (idempotent). Confirmation email re-sent.",
            "reference_listings": related,
            "email": email,
        }

    refund = Refund(
        order_id=order.id,
        request_key=key,
        reason=reason,
        amount_cents=order.total_cents,
        currency=order.currency,
        status="pending_manual",
    )
    ctx.db.add(refund)
    ctx.db.flush()

    amount_str = _format_money(refund.amount_cents, refund.currency)
    email = _auto_email(
        ctx,
        subject=f"Refund opened · {amount_str} for order {order.id}",
        body=(
            f"Hi,\n\nWe have opened refund #{refund.id} on order {order.id}.\n"
            f"Reason on file: {reason}\nAmount: {amount_str}\nStatus: {refund.status}."
            f"{listing_block}\n\n"
            "Funds typically return in 5 business days after approval. We'll email you again "
            "the moment the status changes.\n\n— Atlas Support"
        ),
        label="refund_opened",
    )

    return {
        "ok": True,
        "refund_id": refund.id,
        "status": refund.status,
        "amount": amount_str,
        "message": "Refund recorded. Confirmation email sent. Funds typically return in 5 business days once approved.",
        "reference_listings": related,
        "email": email,
    }


def tool_get_account_info(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    customer = _resolve_customer(ctx.db, ctx)
    if not customer:
        return {
            "ok": False,
            "message": "No authenticated customer is associated with this session.",
        }
    open_orders = (
        ctx.db.query(Order)
        .filter(Order.customer_id == customer.id, Order.status != "Delivered")
        .count()
    )
    total_orders = ctx.db.query(Order).filter(Order.customer_id == customer.id).count()
    return {
        "ok": True,
        "customer_id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "tier": customer.tier,
        "member_since": customer.member_since.date().isoformat(),
        "open_orders": open_orders,
        "total_orders": total_orders,
    }


def tool_search_product_knowledge(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    top_k = int(args.get("top_k", 6))
    if not query:
        return {"ok": False, "error": "query required"}
    settings = get_settings()
    scan_k = min(max(top_k * 3, 12), 24)
    try:
        hits = rag.search_products(query=query, top_k=scan_k)
    except Exception as exc:
        log.exception("search_product_knowledge failed")
        return {"ok": False, "error": f"catalog search unavailable: {exc}"}
    floor = settings.rag_catalog_min_score
    hits = [h for h in hits if float(h.score or 0) >= floor][: max(1, min(top_k, 12))]
    ctx.rag_hits.extend(hits)
    return {
        "ok": True,
        "count": len(hits),
        "results": [
            {
                "asin": h.asin,
                "title": h.title,
                "category": h.category,
                "price": h.price,
                "stars": h.stars,
                "url": h.url,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


def tool_search_policy_knowledge(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    top_k = int(args.get("top_k", 3))
    if not query:
        return {"ok": False, "error": "query required"}
    try:
        hits = rag.search_policies(query=query, top_k=top_k)
    except Exception as exc:
        log.exception("search_policy_knowledge failed")
        return {"ok": False, "error": f"policy search unavailable: {exc}"}
    if not hits:
        return {
            "ok": True,
            "count": 0,
            "results": [],
            "message": "No policy match. The policy KB may not be ingested yet (run policy ingest).",
        }
    return {
        "ok": True,
        "count": len(hits),
        "results": [
            {
                "topic": h.topic,
                "text": h.text,
                "source": h.source,
                "section": h.section,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


def tool_escalate_to_human(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    reason = str(args.get("reason", "")).strip() or "Customer requested escalation"
    priority = str(args.get("priority", "medium")).lower()
    if priority not in {"low", "medium", "high", "critical"}:
        priority = "medium"
    customer = _resolve_customer(ctx.db, ctx)

    related = _related_products(query=reason, top_k=2, ctx=ctx) if reason else []
    listing_lines = _format_listing_lines(related, cap=2)
    rag_note = ("\n\nLikely catalog matches surfaced for the agent:\n" + "\n".join(listing_lines)) if listing_lines else ""
    enriched_reason = (reason + rag_note).strip()

    ticket_id = "TKT-" + "".join(random.choices(string.digits, k=6))
    ticket = SupportTicket(
        id=ticket_id,
        session_id=ctx.session_id,
        customer_id=customer.id if customer else None,
        priority=priority,
        reason=enriched_reason,
        status="open",
    )
    ctx.db.add(ticket)
    ctx.db.flush()
    waits = {"low": "15-20 minutes", "medium": "8-12 minutes", "high": "3-5 minutes", "critical": "under 2 minutes"}

    listing_block = ("\n\nReference listing(s) we'll share with the teammate:\n" + "\n".join(listing_lines)) if listing_lines else ""
    email = _auto_email(
        ctx,
        subject=f"Support ticket {ticket_id} opened · priority {priority}",
        body=(
            f"Hi,\n\nA human teammate has been paged for your case.\n"
            f"Ticket: {ticket_id}\nPriority: {priority}\nReason on file: {reason}\n"
            f"Estimated wait: {waits[priority]}."
            f"{listing_block}\n\n"
            "You'll hear back in this thread shortly. If anything new comes up in the meantime, "
            "just reply and we'll add it to the ticket.\n\n— Atlas Support"
        ),
        label="ticket_opened",
    )

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "priority": priority,
        "estimated_wait": waits[priority],
        "message": f"Ticket {ticket_id} opened. A human agent will reach out shortly. Confirmation email sent.",
        "reference_listings": related,
        "email": email,
    }


def tool_send_customer_email(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", "")).strip()
    if not subject or not body:
        return {"ok": False, "error": "subject and body required"}
    customer = _resolve_customer(ctx.db, ctx)
    to_address = customer.email if customer else None
    if not to_address:
        return {
            "ok": False,
            "error": "No verified customer email available for this session.",
        }
    record = send_email(
        ctx.db,
        to_address=to_address,
        subject=subject,
        body=body,
        session_id=ctx.session_id,
    )
    return {
        "ok": True,
        "email_id": record.id,
        "status": record.status,
        "to": to_address,
    }


TOOL_DISPATCHER = {
    "lookup_order": tool_lookup_order,
    "search_customer_orders": tool_search_customer_orders,
    "process_refund": tool_process_refund,
    "get_account_info": tool_get_account_info,
    "search_product_knowledge": tool_search_product_knowledge,
    "search_policy_knowledge": tool_search_policy_knowledge,
    "escalate_to_human": tool_escalate_to_human,
    "send_customer_email": tool_send_customer_email,
}


def execute_tool(name: str, arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    fn = TOOL_DISPATCHER.get(name)
    if not fn:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return fn(arguments, ctx)
    except Exception as exc:
        log.exception("Tool %s failed", name)
        return {"ok": False, "error": str(exc)}
