from __future__ import annotations

import logging
import re
from typing import List, Tuple

import httpx

from . import tools
from .schemas import ToolInvocation
from .sentiment import detect_intent as _detect_intent
from .settings import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Atlas, a warm, direct, and helpful customer support representative for our e-commerce platform. "
    "You resolve shopping inquiries using the verified system data provided with each message. "
    "CRITICAL: All system data has already been fetched for you. NEVER say you need to check, look up, "
    "or gather information — state the facts directly from the data provided. "
    "NEVER ask the customer for their order number if it appears in the data below. "
    "When system data is provided, state those exact facts — order ID, carrier, dates, "
    "amounts, policy text — and do not add information from outside what is given. "
    "Keep replies concise and to the point."
)

_ORDER_ID_RE = re.compile(r'\bORD-[\w-]+\b', re.IGNORECASE)

_POLICY_KEYWORDS = {
    'return', 'refund', 'shipping', 'warranty', 'guarantee', 'cancel', 'exchange',
    'policy', 'eligible', 'damaged', 'missing', 'lost', 'delivery', 'prime',
    'delivered', 'arrived', 'received', 'package', 'never', 'wrong',
    'described', 'late', 'delayed', 'waiting', 'door', 'porch',
    'neighbor', 'left', 'nowhere', 'find',
}
_PRODUCT_KEYWORDS = {
    'recommend', 'suggest', 'looking for', 'show me', 'find me', 'best',
    'under $', 'cheap', 'buy', 'purchase', 'product', 'headphone', 'laptop',
    'phone', 'camera', 'watch', 'bag', 'shoe', 'keyboard', 'monitor',
    'earbuds', 'speaker', 'tablet', 'charger', 'cable', 'stand', 'desk',
    'chair', 'light', 'lamp', 'game', 'toy', 'headset', 'mouse',
    'printer', 'router', 'backpack', 'suitcase', 'luggage', 'wallet',
    'jacket', 'clothing', 'glasses', 'sunglasses', 'book', 'vacuum',
    'blender', 'coffee', 'pillow', 'mattress', 'towel', 'bottle',
}
_ACCOUNT_KEYWORDS = {'account', 'membership', 'tier', 'profile', 'my info', 'member since',
                     'how many orders', 'open orders', 'total orders', 'order history'}
_REFUND_KEYWORDS = {'refund', 'money back', 'charge back', 'reimbburse', 'reimburse'}
_CANCEL_KEYWORDS = {
    'cancel my order', 'cancel the order', 'cancel order', 'i want to cancel',
    'cancellation', 'cancel it', 'stop my order', "don't want it anymore",
    'no longer want', 'undo my order', 'abort',
}
# Phrases that CONTAIN "cancel" but are NOT order-cancellation requests
_CANCEL_FALSE_POSITIVES = {'noise cancel', 'noise-cancel', 'active cancel'}


def _fmt_order(result: dict) -> str:
    if not result.get("ok") or not result.get("found"):
        oid = result.get("order_id", "N/A")
        return f"Order Number: {oid}\nStatus: Not found. {result.get('message', '')}"
    oid = result.get("order_id", "N/A")
    items = result.get("items", [])
    item_list = ", ".join(f"{i.get('title', 'Item')} x{i.get('qty', 1)}" for i in items)
    lines = [
        f"Order Number: {oid}",
        f"Order Status: {result.get('status', 'N/A')}",
        f"Shipping Method: {result.get('carrier', 'N/A')}",
        f"Tracking Number: {result.get('tracking', 'N/A')}",
        f"Delivery Date: {result.get('eta', 'N/A')}",
        f"Items Ordered: {item_list}",
        f"Order Total: {result.get('total', 'N/A')}",
    ]
    return "\n".join(lines)


def _extract_order_id_from_history(history: List[dict]) -> str:
    """Scan the last few history messages for an order ID when none is in the current message."""
    for msg in reversed(history[-6:]):
        content = msg.get("content") or ""
        m = _ORDER_ID_RE.search(content)
        if m:
            return m.group(0).upper()
    return ""


def _route_tools(
    message: str,
    ctx: tools.ToolContext,
    history: List[dict] = [],
) -> Tuple[List[ToolInvocation], str]:
    """Deterministically call tools based on message content.
    Returns (invocations, context_block_to_inject)."""
    msg_lower = message.lower()
    invocations: List[ToolInvocation] = []
    blocks: List[str] = []

    order_match = _ORDER_ID_RE.search(message)

    # Bug fix: refund takes priority over cancel when both keywords appear
    has_refund = any(k in msg_lower for k in _REFUND_KEYWORDS)
    has_cancel = (
        any(k in msg_lower for k in _CANCEL_KEYWORDS)
        and not any(fp in msg_lower for fp in _CANCEL_FALSE_POSITIVES)
        and not has_refund
    )

    # Only pull order ID from history when the current message is actually order-related.
    # Use word boundaries to avoid "ship" matching "membership", etc.
    _ORDER_CONTEXT_RE = re.compile(
        r'\b(order|track|tracking|shipped|shipping|deliver|delivered|delivery|'
        r'cancel|cancell?ation|refund|return|package|parcel|status|carrier|'
        r'eta|transit|estimated|arrival|where is|where\'s|when will|when does|'
        r'arrive|arrived|item|same order|that order|my order)\b',
        re.IGNORECASE,
    )
    if not order_match and history and _ORDER_CONTEXT_RE.search(message):
        prior_id = _extract_order_id_from_history(history)
        if prior_id:
            order_match = type('M', (), {'group': lambda self, n: prior_id})()

    # Cancel order
    if order_match and has_cancel:
        order_id = order_match.group(0).upper()
        result = tools.execute_tool("cancel_order", {"order_id": order_id, "reason": message}, ctx)
        invocations.append(ToolInvocation(name="cancel_order", arguments={"order_id": order_id}, result=result))
        msg_txt = result.get("message", "N/A")
        ok = result.get("ok", False)
        blocks.append(
            f"Cancellation Status: {'Successful' if ok else 'Failed'}\n"
            f"Cancellation Message: {msg_txt}"
        )

    # Refund with known order ID
    elif order_match and has_refund:
        order_id = order_match.group(0).upper()
        result = tools.execute_tool("lookup_order", {"order_id": order_id}, ctx)
        invocations.append(ToolInvocation(name="lookup_order", arguments={"order_id": order_id}, result=result))
        blocks.append(_fmt_order(result))
        refund_result = tools.execute_tool("process_refund", {"order_id": order_id, "reason": message}, ctx)
        invocations.append(ToolInvocation(name="process_refund", arguments={"order_id": order_id, "reason": message}, result=refund_result))
        blocks.append(
            f"Refund ID: {refund_result.get('refund_id', 'N/A')}\n"
            f"Refund Amount: {refund_result.get('amount', 'N/A')}\n"
            f"Refund Status: {refund_result.get('status', 'N/A')}"
        )

    # Order lookup
    elif order_match:
        order_id = order_match.group(0).upper()
        result = tools.execute_tool("lookup_order", {"order_id": order_id}, ctx)
        invocations.append(ToolInvocation(name="lookup_order", arguments={"order_id": order_id}, result=result))
        blocks.append(_fmt_order(result))

    # Policy question — always search if keywords match (even with order ID)
    if any(k in msg_lower for k in _POLICY_KEYWORDS):
        result = tools.execute_tool("search_policy_knowledge", {"query": message, "top_k": 3}, ctx)
        invocations.append(ToolInvocation(name="search_policy_knowledge", arguments={"query": message}, result=result))
        for r in result.get("results", []):
            blocks.append(f"Policy ({r.get('topic','?')} - {r.get('section','?')}): {r.get('text','')}")

    # Product search
    if any(k in msg_lower for k in _PRODUCT_KEYWORDS):
        result = tools.execute_tool("search_product_knowledge", {"query": message, "top_k": 4}, ctx)
        invocations.append(ToolInvocation(name="search_product_knowledge", arguments={"query": message}, result=result))
        for r in result.get("results", []):
            blocks.append(f"- {r.get('title','?')} | {r.get('category','?')} | ${r.get('price',0)} | {r.get('stars',0)} stars")

    # Account info
    if any(k in msg_lower for k in _ACCOUNT_KEYWORDS):
        result = tools.execute_tool("get_account_info", {}, ctx)
        invocations.append(ToolInvocation(name="get_account_info", arguments={}, result=result))
        blocks.append(
            f"Customer Name: {result.get('name', 'N/A')}\n"
            f"Account Type: {result.get('tier', 'N/A')}\n"
            f"Member Since: {result.get('member_since', 'N/A')}\n"
            f"Open Orders: {result.get('open_orders', 0)}\n"
            f"Total Orders: {result.get('total_orders', 0)}"
        )

    # ── Order context carryforward ────────────────────────────────────────────
    # If the customer is identified, no tools fired yet, but there is a recent
    # order in history, re-run lookup_order so the LLM can answer follow-up
    # questions (e.g. "Who is the carrier?", "Is it late?") without the user
    # having to repeat the order number.
    _ORDER_FOLLOWUP_RE = re.compile(
        r'\b(carrier|when|eta|arrival|transit|tracking|estimated|update|'
        r'late|early|delayed|status|same order|that order|expedite|delivery date|'
        r'where|how long|still|yet|received|expect|location|it is|it\'s)\b',
        re.IGNORECASE,
    )
    if (not any(inv.name == "lookup_order" for inv in invocations)
            and ctx.customer_id
            and history
            and _ORDER_FOLLOWUP_RE.search(message)):
        prior_id = _extract_order_id_from_history(history)
        if prior_id:
            result = tools.execute_tool("lookup_order", {"order_id": prior_id}, ctx)
            invocations.append(ToolInvocation(
                name="lookup_order",
                arguments={"order_id": prior_id},
                result=result,
            ))
            blocks.append(_fmt_order(result))

    context_block = "\n".join(blocks)
    return invocations, context_block



_STATIC_PLACEHOLDERS = {
    "website_url": "website",
    "online_company_portal_info": "website",
    "online_order_interaction": "Your Orders",
    "customer_support_hours": "business hours",
    "customer_support_phone_number": "our contact page",
    "company_name": "our platform",
}


def _fill_placeholders(text: str, invocations: "List[ToolInvocation]") -> str:
    """Substitute {{placeholder}} tokens with real values from tool results."""
    values: dict = dict(_STATIC_PLACEHOLDERS)
    for inv in invocations:
        r = inv.result or {}
        if inv.name in {"lookup_order", "cancel_order", "process_refund"} and r.get("order_id"):
            values["order_number"] = r["order_id"]
            values["order_id"] = r["order_id"]
        if inv.name == "lookup_order":
            carrier = r.get("carrier", "")
            values.update({
                "status": r.get("status", ""),
                "carrier": carrier,
                "shipping_method": carrier,
                "shipping_carrier": carrier,
                "tracking": r.get("tracking", ""),
                "tracking_number": r.get("tracking", ""),
                "eta": r.get("eta", ""),
                "delivery_date": r.get("eta", ""),
                "expected_delivery": r.get("eta", ""),
                "total": r.get("total", ""),
            })
        elif inv.name == "process_refund":
            values.update({
                "refund_id": r.get("refund_id", ""),
                "amount": r.get("amount", ""),
                "refund_amount": r.get("amount", ""),
            })
        elif inv.name == "cancel_order":
            values["cancel_status"] = "successful" if r.get("ok") else "failed"
        elif inv.name == "get_account_info":
            tier = r.get("tier", "")
            name = r.get("name", "")
            since = r.get("member_since", "")
            values.update({
                "name": name,
                "customer_name": name,
                "tier": tier,
                "account_type": tier,
                "account_tier": tier,
                "membership_level": tier,
                "membership_type": tier,
                "membership": tier,
                "member_since": since,
                "membership_since": since,
                "open_orders": str(r.get("open_orders", "")),
                "total_orders": str(r.get("total_orders", "")),
            })

    def _sub(m: re.Match) -> str:
        key = m.group(1).strip().lower().replace(" ", "_")
        val = values[key] if key in values else m.group(1).strip().lower()
        # Avoid "ORD-ORD-12345" when model outputs "ORD-{{Order Number}}"
        prefix = text[max(0, m.start() - 4): m.start()]
        if prefix.upper().endswith("ORD-") and isinstance(val, str) and val.upper().startswith("ORD-"):
            val = val[4:]
        return val

    return re.sub(r'\{\{([^}]+)\}\}', _sub, text)


_STALL_RE = re.compile(
    r'\b(let me (check|look|find|gather|pull|verify|search)|'
    r'allow me (a moment|to check|to look|to verify)|'
    r'(I|I\'ll) (need to|would need to|will need to|am going to) (check|look|verify|gather|pull)|'
    r'please (hold|wait|give me a moment)|'
    r'I\'m (checking|looking|pulling|verifying)|'
    r'I (don\'t|do not) have that information)\b',
    re.IGNORECASE,
)

_PLEASANTRY_RE = re.compile(
    r'\b(thank(s| you)|appreciate|great|awesome|perfect|wonderful|'
    r'that\'s all|that is all|no more|all good|you\'ve been|you have been)\b',
    re.IGNORECASE,
)


def _template_from_invocations(invocations: "List[ToolInvocation]") -> str:
    """Deterministic fallback when LLM stalls: build response directly from tool results."""
    parts = []
    for inv in invocations:
        r = inv.result or {}
        if inv.name == "lookup_order":
            if r.get("found"):
                oid = r.get("order_id", "your order")
                items = r.get("items", [])
                item_str = ", ".join(f"{i.get('title','Item')} x{i.get('qty',1)}" for i in items)
                parts.append(
                    f"Here's the latest on **{oid}**: "
                    f"Status is **{r.get('status','N/A')}**, "
                    f"shipped via {r.get('carrier','N/A')} "
                    f"(tracking: `{r.get('tracking','N/A')}`), "
                    f"estimated delivery **{r.get('eta','N/A')}**."
                    + (f" Items: {item_str}." if item_str else "")
                )
            else:
                parts.append(f"I couldn't find order {r.get('order_id','requested')} in our system.")
        elif inv.name == "process_refund":
            if r.get("ok"):
                parts.append(
                    f"Your refund of **{r.get('amount','N/A')}** has been initiated "
                    f"(Refund ID: {r.get('refund_id','N/A')}). "
                    f"Status: **{r.get('status','processing')}**. "
                    "You'll receive a confirmation email shortly."
                )
            else:
                parts.append(
                    f"We weren't able to process the refund at this time. "
                    f"{r.get('message','Please contact our support team for assistance.')}"
                )
        elif inv.name == "cancel_order":
            if r.get("ok"):
                parts.append(f"Your order has been **successfully cancelled**. {r.get('message','')}")
            else:
                parts.append(
                    f"We're unable to cancel this order. "
                    f"{r.get('message','It may have already shipped — you can return it once delivered.')}"
                )
        elif inv.name == "get_account_info":
            name = r.get("name", "")
            tier = r.get("tier", "")
            since = r.get("member_since", "")
            open_o = r.get("open_orders", 0)
            total_o = r.get("total_orders", 0)
            if name or tier:
                parts.append(
                    f"Here's your account summary, **{name}**: "
                    f"you're a **{tier}** member since {since}, "
                    f"with {open_o} open order(s) and {total_o} total orders."
                )
    if parts:
        return " ".join(parts) + " Is there anything else I can help you with?"
    return ""


def _reply_uses_order_data(reply: str, invocations: "List[ToolInvocation]") -> bool:
    """Return True if the reply contains at least one concrete value from lookup_order."""
    for inv in invocations:
        if inv.name != "lookup_order":
            continue
        r = inv.result or {}
        if not r.get("found"):
            return True  # not-found message is fine as-is
        for key in ("tracking", "eta", "status", "carrier"):
            val = str(r.get(key, "")).strip()
            if val and val.lower() not in ("n/a", "none", "") and val in reply:
                return True
    return False


def _reply_uses_account_data(reply: str, invocations: "List[ToolInvocation]") -> bool:
    """Return True if the reply contains at least one concrete value from get_account_info."""
    for inv in invocations:
        if inv.name != "get_account_info":
            continue
        r = inv.result or {}
        for key in ("tier", "name", "member_since"):
            val = str(r.get(key, "")).strip()
            if val and val.lower() not in ("n/a", "none", "") and val.lower() in reply.lower():
                return True
    return False


def _clean_reply(text: str) -> str:
    text = re.sub(r'\[TOOL_CALL\].*?(\}|\n|$)', '', text, flags=re.DOTALL)
    text = re.sub(r'\[TOOL_RESULT\].*?(\}|\n|$)', '', text, flags=re.DOTALL)
    return text.strip()


def _messages_to_phi4_prompt(messages: List[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"<|system|>\n{content}<|end|>")
        elif role == "user":
            parts.append(f"<|user|>\n{content}<|end|>")
        elif role == "assistant":
            parts.append(f"<|assistant|>\n{content}<|end|>")
    parts.append("<|assistant|>")
    return "\n".join(parts)


def _call_hf(messages: List[dict], max_new_tokens: int = 256) -> str:
    settings = get_settings()
    prompt = _messages_to_phi4_prompt(messages)
    try:
        resp = httpx.post(
            settings.hf_endpoint_url,
            headers={"Authorization": f"Bearer {settings.hf_token}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": max_new_tokens,
                    "temperature": 0.1,
                    "return_full_text": False,
                },
            },
            timeout=settings.llm_timeout_seconds,
        )
        if not resp.is_success:
            log.error("HF 4xx body: %s", resp.text[:500])
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list):
            return result[0].get("generated_text", "")
        return result.get("generated_text", "")
    except Exception as exc:
        log.error("HF endpoint error: %s", exc)
        raise


def _augment_system(base: str, sentiment: str, cumulative: str) -> str:
    extras = []
    if cumulative == "frustrated":
        extras.append(
            "CRITICAL: This customer has been frustrated across multiple turns. "
            "Lead with sincere acknowledgement and resolve as fast as possible."
        )
    elif cumulative == "negative" or sentiment in {"negative", "frustrated"}:
        extras.append("Note: The customer is unhappy. Be extra empathetic and solution-focused.")
    if not extras:
        return base
    return base + "\n\n" + "\n".join(extras)


def run_agent(
    *,
    user_message: str,
    history: List[dict],
    sentiment: str,
    cumulative: str,
    ctx: tools.ToolContext,
) -> Tuple[str, List[ToolInvocation]]:
    # Step 1: deterministically call tools based on message content
    invocations, tool_context = _route_tools(user_message, ctx, history)

    # Escalation intent — respond directly, no LLM needed
    if _detect_intent(user_message) == "escalate_to_human":
        return (
            "I completely understand — I'm connecting you with a human support agent right now. "
            "Please hold on. A member of our team will be with you shortly and will have full context of our conversation."
        ), invocations

    # If nothing was retrieved, skip the LLM — it will hallucinate without grounding
    if not invocations:
        if _PLEASANTRY_RE.search(user_message):
            return (
                "You're very welcome! I'm glad I could help. "
                "If you ever need anything else, don't hesitate to reach out. Have a great day!"
            ), invocations
        if sentiment == "frustrated" or cumulative == "frustrated":
            return (
                "I'm really sorry you're having this experience — that's completely understandable. "
                "I want to make sure you get the right help. Let me connect you with a member of our "
                "support team who can look into this personally. Please hold on."
            ), invocations
        return (
            "I don't have specific information about that in our system. "
            "For detailed help, please contact our support team directly or visit our help center."
        ), invocations

    # Step 2: build prompt — inject tool results so model only needs to write the response
    system = _augment_system(SYSTEM_PROMPT, sentiment, cumulative)

    readable_context = tool_context
    if readable_context:
        augmented_user = (
            f"{user_message}\n\n"
            f"Here is the verified data from our systems. Use ONLY these facts in your reply:\n{readable_context}"
        )
    else:
        augmented_user = user_message

    messages: List[dict] = [{"role": "system", "content": system}]
    for h in history[-8:]:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": augmented_user})

    reply = _call_hf(messages, max_new_tokens=300)
    reply = _fill_placeholders(reply, invocations)
    reply = _clean_reply(reply)

    # Safety net 1: LLM explicitly stalls ("let me check", "please allow me", etc.)
    if _STALL_RE.search(reply):
        template = _template_from_invocations(invocations)
        if template:
            log.info("LLM stall detected — using template response")
            return template, invocations

    # Safety net 2: LLM called lookup_order but reply contains none of the actual data values
    has_order_lookup = any(inv.name == "lookup_order" for inv in invocations)
    if has_order_lookup and not _reply_uses_order_data(reply, invocations):
        template = _template_from_invocations(invocations)
        if template:
            log.info("LLM ignored order data — using template response")
            return template, invocations

    # Safety net 3: LLM called get_account_info but reply contains none of the account values
    has_account = any(inv.name == "get_account_info" for inv in invocations)
    if has_account and not _reply_uses_account_data(reply, invocations):
        template = _template_from_invocations(invocations)
        if template:
            log.info("LLM ignored account data — using template response")
            return template, invocations

    return reply or "I'm here to help — could you clarify what you need?", invocations
