from __future__ import annotations

import re
from typing import Iterable

SENTIMENT_RULES: dict[str, list[str]] = {
    "frustrated": [
        "unacceptable", "ridiculous", "terrible", "awful", "horrible",
        "worst", "angry", "furious", "disgusting", "outrageous", "pathetic",
        "useless", "incompetent", "scam", "fraud", "rip off", "wasted",
        "never again", "cancel everything", "fed up", "speak to a manager",
        "frustrated", "so frustrated", "very frustrated", "extremely frustrated",
        "annoyed", "so annoyed", "very annoyed", "pissed", "livid",
        "unbelievable", "absurd", "disgrace", "joke", "embarrassing",
    ],
    # Only genuine expressions of dissatisfaction — not topic/request words like
    # "refund", "return", "cancel" which appear in neutral policy questions.
    "negative": [
        "problem", "issue", "wrong", "broken", "missing", "late", "delayed",
        "damaged", "error", "failed", "not working", "doesn't work",
        "unable", "bad", "poor", "unhappy", "still haven't", "never received",
        "disappointed", "upset", "not happy", "not satisfied",
    ],
    "positive": [
        "thank", "thanks", "great", "excellent", "amazing", "wonderful",
        "perfect", "love", "happy", "satisfied", "appreciate", "fantastic",
        "awesome", "brilliant", "helpful", "pleased",
    ],
}

INTENT_RULES: dict[str, list[str]] = {
    "track_order": ["where is my order", "track", "tracking", "order status", "where's my", "shipped"],
    # Use phrases/boundaries to avoid "noise cancelling" → cancel_order
    "cancel_order": ["cancel my order", "cancel the order", "cancel order", "i want to cancel",
                     "cancellation", "cancel it", "stop my order", "don't want it anymore",
                     "no longer want"],
    "return_item": ["return", "send back", "returning"],
    "refund_request": ["refund", "money back", "charge back", "reimburse"],
    "payment_issue": ["payment", "charged", "billing", "invoice", "credit card", "double charge"],
    "account_access": ["login", "log in", "password", "locked out", "sign in"],
    "delivery_problem": ["delivery", "delivered", "not delivered", "package", "arrived damaged", "never arrived"],
    "product_inquiry": ["does it", "specifications", "compatible", "features", "tell me about", "recommend"],
    "product_defect": ["broken", "defective", "defect", "faulty", "stopped working"],
    "wrong_item": ["wrong item", "wrong product", "received wrong", "incorrect item"],
    "missing_item": ["missing", "didn't receive", "not in box", "wasn't included"],
    "complaint": ["complaint", "complain", "terrible service", "worst service"],
    "compliment": ["thank you", "great service", "excellent service"],
    "discount_inquiry": ["discount", "coupon", "promo", "promotion", "deal", "offer", "voucher"],
    "check_stock": ["in stock", "available", "availability", "do you have"],
    "escalate_to_human": [
        "speak to human", "talk to agent", "real person", "supervisor", "manager", "human agent",
        "speak with someone", "talk to someone", "speak to someone", "talk to a person",
        "speak to a person", "speak with a person", "speak with a human", "talk to a human",
        "connect me to", "transfer me", "live agent", "live person", "live support",
        "want to speak", "want to talk", "like to speak", "like to talk",
        "need to speak with", "need to talk to",
    ],
    "shipping_options": ["shipping", "how long", "delivery time", "express", "overnight", "free shipping"],
}

# Phrases that look like they contain a keyword but are product/feature descriptions.
# Strip these from the text before sentiment/intent matching.
_NEUTRAL_PRODUCT_PHRASES = re.compile(
    r'\bnoise[- ]cancel(?:l?ing|l?ation|l?er)?\b'
    r'|\bactive noise\b'
    r'|\bnc headphones?\b',
    re.IGNORECASE,
)


def _strip_product_phrases(text: str) -> str:
    return _NEUTRAL_PRODUCT_PHRASES.sub('', text)


def detect_sentiment(text: str) -> str:
    t = _strip_product_phrases(text).lower()
    for label in ("frustrated", "negative", "positive"):
        if any(kw in t for kw in SENTIMENT_RULES[label]):
            return label
    return "neutral"


def detect_intent(text: str) -> str:
    t = _strip_product_phrases(text).lower()
    for intent, kws in INTENT_RULES.items():
        if any(kw in t for kw in kws):
            return intent
    return "general_inquiry"


def cumulative_sentiment(history: Iterable[str]) -> str:
    counts = {"frustrated": 0, "negative": 0, "positive": 0, "neutral": 0}
    for s in history:
        counts[s] = counts.get(s, 0) + 1
    if counts["frustrated"] >= 1:
        return "frustrated"
    # Require 3 negative turns (not 2) before treating as frustrated —
    # prevents policy FAQ chains from triggering premature escalation.
    if counts["negative"] >= 3:
        return "frustrated"
    if counts["negative"] >= 1:
        return "negative"
    if counts["positive"] >= 2:
        return "positive"
    return "neutral"


def should_escalate(sentiment: str, cumulative: str, intent: str) -> bool:
    # Only escalate on explicit frustration or escalation intent — not just "negative" sentiment,
    # which fires on ordinary problem-reporting turns.
    if sentiment == "frustrated":
        return True
    if cumulative == "frustrated":
        return True
    if intent in {"complaint", "escalate_to_human"}:
        return True
    return False
