"""Dashboard database: users, roles, which dashboard sections each role sees,
login sessions and a sign-in log.

PostgreSQL in normal use (DATABASE_URL, e.g.
postgresql+psycopg://ndhd_app:...@127.0.0.1:5432/ndhd). The models use only
portable column types, so tests run the same code on in-memory SQLite.
Schema changes go through Alembic (migrations/), never create_all() on a
real database.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

# Dashboard sections a role can be allowed to see. Each maps to API routes
# (api/deps.require_section) and to WebSocket message types (api/ws.py).
SECTIONS = {
    "account": "Alpaca paper account: balances, positions, open orders",
    "strategy": "Stock and wheel strategy panels, risk panel, engine config",
    "alerts": "Alerts and the live engine log",
    "congress": "Congress trades research",
    "watch": "Watch-only list",
    "robinhood": "Robinhood snapshot (personal brokerage data)",
    "control": "Orders, closing positions, the kill switch",
}

# Seeded by the first migration. Admin always gets every section, whatever
# role_sections says, so an admin can never lock themselves out.
ADMIN_ROLE = "admin"
VIEWER_ROLE = "viewer"
VIEWER_DEFAULT_SECTIONS = ("account", "strategy", "alerts", "congress", "watch")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")

    sections: Mapped[list["RoleSection"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class RoleSection(Base):
    __tablename__ = "role_sections"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    section: Mapped[str] = mapped_column(String(50), primary_key=True)

    role: Mapped[Role] = relationship(back_populates="sections")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[Role] = relationship()


class LoginSession(Base):
    """One signed-in browser. The cookie holds a random token; only its
    SHA-256 is stored, so a database leak doesn't hand out live sessions."""

    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship()


class LoginEvent(Base):
    """Every sign-in attempt, kept for the audit trail and for throttling
    repeated failures."""

    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


_engine: Optional[Engine] = None
_sessionmaker: Optional[sessionmaker] = None


def database_url() -> Optional[str]:
    return os.getenv("DATABASE_URL") or None


def configure(url: Optional[str] = None, engine: Optional[Engine] = None) -> None:
    """Point the module at a database. Called lazily from session(); tests
    pass their own engine."""
    global _engine, _sessionmaker
    if engine is None:
        url = url or database_url()
        if not url:
            raise RuntimeError("DATABASE_URL is not set.")
        engine = create_engine(url, pool_pre_ping=True)
    _engine = engine
    _sessionmaker = sessionmaker(bind=engine, expire_on_commit=False)


def session() -> Session:
    if _sessionmaker is None:
        configure()
    return _sessionmaker()


def role_sections(db: Session, role: Role) -> set[str]:
    if role.name == ADMIN_ROLE:
        return set(SECTIONS)
    return set(db.scalars(select(RoleSection.section).where(RoleSection.role_id == role.id)))
