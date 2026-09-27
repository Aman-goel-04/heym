"""Distinguish an owner-granted workflow share from one created only to remember a
personal folder placement for a workflow the user reaches through a team share.

Revision ID: 128_workflow_share_explicit_flag
Revises: 127_add_dashboard_shares
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "128_workflow_share_explicit_flag"
down_revision: Union[str, None] = "127_add_dashboard_shares"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_shares",
        sa.Column("is_explicit_share", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("workflow_shares", "is_explicit_share")
