from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None


class ToolInvocation(BaseModel):
    name: str
    arguments: dict
    result: dict


class RagSource(BaseModel):
    asin: str
    title: str
    score: float
    category: Optional[str] = None
    price: Optional[float] = None


class ChatResponse(BaseModel):
    reply: str
    sentiment: str
    intent: str
    escalated: bool = False
    tools_called: List[ToolInvocation] = []
    rag_sources: List[RagSource] = []
    session_id: str


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, Any]
