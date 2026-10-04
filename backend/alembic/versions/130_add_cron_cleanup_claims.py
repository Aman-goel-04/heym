"""add cron cleanup claims

Revision ID: 130_add_cron_cleanup_claims
Revises: 129_workflow_share_permission
Create Date: 2026-10-04 00:00:00.000000

Note: alembic_version.version_num is varchar(32), so the revision id must stay
within 32 characters.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "130_add_cron_cleanup_claims"
down_revision: Union[str, None] = "129_workflow_share_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cron_cleanup_claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_name", sa.String(length=128), nullable=False),
        sa.Column("slot_date", sa.String(length=8), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("job_name", "slot_date", name="uq_cron_cleanup_claim"),
    )
    op.create_index("ix_cron_cleanup_claims_created_at", "cron_cleanup_claims", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_cron_cleanup_claims_created_at", table_name="cron_cleanup_claims")
    op.drop_table("cron_cleanup_claims")
