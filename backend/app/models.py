from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="standard")
    member_since: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="processing")
    carrier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tracking: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    eta: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="orders")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("order_id", "request_key", name="uq_refunds_order_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(32), ForeignKey("orders.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(32), default="pending_manual")
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    order: Mapped[Order] = relationship(back_populates="refunds")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Session(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=True
    )
    cumulative_sentiment: Mapped[str] = mapped_column(String(16), default="neutral")
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    tools_called: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    to_address: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    provider_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
