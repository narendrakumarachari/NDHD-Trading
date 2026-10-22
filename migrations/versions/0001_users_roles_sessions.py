"""Users, roles, role sections, login sessions and the sign-in log.

Seeds two roles: admin (every section, always) and viewer (everything but
the Robinhood snapshot and the controls).

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

from ndhd.db import VIEWER_DEFAULT_SECTIONS

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
    )
    role_sections = op.create_table(
        "role_sections",
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("section", sa.String(50), primary_key=True),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table(
        "login_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(300), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_login_events_username", "login_events", ["username"])
    op.create_index("ix_login_events_at", "login_events", ["at"])

    op.bulk_insert(roles, [
        {"id": 1, "name": "admin", "description": "Everything, including Robinhood and the controls"},
        {"id": 2, "name": "viewer", "description": "Read-only dashboard without Robinhood or controls"},
    ])
    op.bulk_insert(role_sections, [{"role_id": 2, "section": s} for s in VIEWER_DEFAULT_SECTIONS])
    if op.get_bind().dialect.name == "postgresql":
        # Explicit ids above; move the sequence past them for future roles.
        op.execute("SELECT setval(pg_get_serial_sequence('roles', 'id'), 2)")


def downgrade() -> None:
    op.drop_table("login_events")
    op.drop_table("login_sessions")
    op.drop_table("users")
    op.drop_table("role_sections")
    op.drop_table("roles")
