"""Retain archive provenance and independently dated source metadata."""

import sqlalchemy as sa
from alembic import op

revision = "73b621"
down_revision = "2fdede418ca2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("source_snapshots", sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("source_snapshots", "source_metadata")
