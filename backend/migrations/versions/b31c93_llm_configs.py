"""Encrypted owner-bound LLM settings and revision-bound verification."""

import sqlalchemy as sa
from alembic import op

revision = "b31c93"
down_revision = "a28c52"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=True),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("verified_version", sa.Integer(), nullable=True),
        sa.Column("verified_binding", sa.String(64), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "provider", name="uq_llm_config_owner_provider"),
    )
    op.create_index("ix_llm_configs_owner_id", "llm_configs", ["owner_id"])
    op.create_index("uq_llm_config_active_owner", "llm_configs", ["owner_id"], unique=True,
                    postgresql_where=sa.text("is_active = true"), sqlite_where=sa.text("is_active = 1"))


def downgrade():
    op.drop_table("llm_configs")
