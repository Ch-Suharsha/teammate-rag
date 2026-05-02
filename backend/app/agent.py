from __future__ import annotations

import json
import logging
from typing import List, Tuple

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from . import tools
from .schemas import ToolInvocation
from .settings import get_settings

log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are Atlas, a senior customer support specialist for an e-commerce store.\n"
    "You can call tools to read live order data, process refunds, look up the product catalog, "
    "search the policy knowledge base, open escalation tickets, and send confirmation emails.\n"
    "\n"
    "Formatting:\n"
    "- Reply in concise GitHub-flavoured Markdown. Use **bold** for the key fact, bullet lists for line items, and `code` for IDs/SKUs/links.\n"
    "- Never wrap the entire reply in a code block. Do not include raw JSON unless the user explicitly asks.\n"
    "\n"
    "Tool policy:\n"
    "- Always use tools instead of guessing. Never invent order IDs, prices, statuses, ETAs, product facts, or policies.\n"
    "- Product, recommendation, comparison, or 'what is this' question -> call search_product_knowledge FIRST.\n"
    "- 'Did I order X?', 'anything with Y in my orders?', or no order id given -> call search_customer_orders FIRST with keywords from the user (it reads Postgres, not the catalog). NEVER guess an order_id such as ORD-88210 unless the user supplied it.\n"
    "- Policy / eligibility / timeline / warranty / shipping / account question -> call search_policy_knowledge FIRST and quote it (cite the topic).\n"
    "- Refund flow: search_customer_orders if order_id unknown, else lookup_order, confirm ownership, then process_refund. Never claim a refund was issued unless the tool returned ok=true.\n"
    "- After process_refund or escalate_to_human succeeds, briefly mention the auto-email was sent. Do NOT call send_customer_email for these confirmations.\n"
    "- send_customer_email is only for ad-hoc follow-ups the customer explicitly requests.\n"
    "- If lookup_order returns related_products, weave at most 2 in as 'You may also like…' with **bold** title and price.\n"
    "- If lookup_order returns found:false with did_you_mean, ask the customer 'Did you mean one of these?' and list the suggestions instead of dead-ending.\n"
    "- If process_refund / escalate_to_human return reference_listings, mention each one briefly. "
    "If reference_listings is empty or missing, do NOT invent 'You may also like' bullets — cite only retrieved rows.\n"
    "- Be concise, warm, and specific. Acknowledge emotion when the customer is upset. Ask only one clarifying question if absolutely necessary.\n"
    "- Cite catalog facts as 'according to the listing'. Cite policy facts with the topic name (e.g. 'per our Returns policy')."
)


def _client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


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
    settings = get_settings()
    client = _client()

    messages: List[ChatCompletionMessageParam] = [
        {"role": "system", "content": _augment_system(SYSTEM_PROMPT, sentiment, cumulative)},
    ]
    for h in history:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    invocations: List[ToolInvocation] = []

    for step in range(settings.agent_max_steps):
        completion = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=settings.llm_temperature,
        )
        choice = completion.choices[0]
        msg = choice.message

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return (msg.content or "").strip(), invocations

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = tools.execute_tool(name, args, ctx)
            invocations.append(ToolInvocation(name=name, arguments=args, result=result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    log.warning("Agent hit max_steps=%s without final answer", settings.agent_max_steps)
    return (
        "I'm still working on this. Let me hand you over to a teammate so you don't have to wait.",
        invocations,
    )
