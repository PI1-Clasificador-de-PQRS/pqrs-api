"""Initial schema: organizations, users, sessions, api_keys, classifications

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _pk() -> sa.Column:
    return sa.Column(
        "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        _pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
    )

    op.create_table(
        "users",
        _pk(),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_index("ix_users_org_id", "users", ["org_id"])

    op.create_table(
        "sessions",
        _pk(),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _created_at(),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "api_keys",
        _pk(),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])

    op.create_table(
        "classifications",
        _pk(),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("text_anonymized", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("engine", sa.String(20), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("external_ref", sa.String(100), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id"), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "category IN ('Petición', 'Reclamo', 'Denuncia', 'Solicitud de información')",
            name="ck_classifications_category",
        ),
        sa.CheckConstraint("engine IN ('llm', 'classifier')", name="ck_classifications_engine"),
        sa.CheckConstraint(
            "source IN ('ui', 'ui_batch', 'api')", name="ck_classifications_source"
        ),
    )
    op.create_index(
        "ix_classifications_org_created_at", "classifications", ["org_id", "created_at"]
    )
    op.create_index("ix_classifications_org_batch_id", "classifications", ["org_id", "batch_id"])


def downgrade() -> None:
    op.drop_table("classifications")
    op.drop_table("api_keys")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("organizations")
