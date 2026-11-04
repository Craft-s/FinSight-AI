"""Store annual and quarterly metrics without period collisions."""

import sqlalchemy as sa
from alembic import op

revision = "a28c52"
down_revision = "73b621"
branch_labels = None
depends_on = None

LEGACY_COLUMNS = {"company_id", "metric_key", "period_end", "rule_version"}
CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade():
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints("normalized_metrics")
    old_name = next(c["name"] for c in constraints if set(c["column_names"]) == LEGACY_COLUMNS)
    with op.batch_alter_table("normalized_metrics", naming_convention=CONVENTION) as batch:
        batch.drop_constraint(old_name or "uq_normalized_metrics_company_id", type_="unique")
        batch.add_column(sa.Column("period_type", sa.String(10), nullable=False, server_default="annual"))
        batch.add_column(sa.Column("period_start", sa.String(10), nullable=True))
        batch.add_column(sa.Column("fiscal_quarter", sa.Integer(), nullable=True))
        batch.create_unique_constraint(
            "uq_normalized_metrics_period",
            ["company_id", "metric_key", "period_type", "period_end", "rule_version"],
        )
        batch.create_index("ix_metric_period_lookup", ["company_id", "period_type", "period_end"])
    metrics = sa.table(
        "normalized_metrics", sa.column("id", sa.String()),
        sa.column("payload", sa.JSON()), sa.column("period_start", sa.String()),
    )
    for row in bind.execute(sa.select(metrics.c.id, metrics.c.payload)):
        bind.execute(metrics.update().where(metrics.c.id == row.id).values(
            period_start=(row.payload or {}).get("period_start"),
        ))


def downgrade():
    # Quarterly rows cannot be represented by the stage-one schema. Raw source
    # snapshots and financial facts are retained and can rematerialize them.
    op.execute(sa.text("DELETE FROM normalized_metrics WHERE period_type <> 'annual'"))
    with op.batch_alter_table("normalized_metrics", naming_convention=CONVENTION) as batch:
        batch.drop_index("ix_metric_period_lookup")
        batch.drop_constraint("uq_normalized_metrics_period", type_="unique")
        batch.drop_column("fiscal_quarter")
        batch.drop_column("period_start")
        batch.drop_column("period_type")
        batch.create_unique_constraint("uq_normalized_metrics_legacy", list(sorted(LEGACY_COLUMNS)))
