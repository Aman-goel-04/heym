"""add work integration

Revision ID: 131_add_work_integration
Revises: 130_add_cron_cleanup_claims
Create Date: 2026-10-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "131_add_work_integration"
down_revision: Union[str, None] = "130_add_cron_cleanup_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_integrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("work_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("key_hash", sa.String(length=64), nullable=True),
        sa.Column("key_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handshake_nonce", sa.String(length=128), nullable=True),
        sa.Column("handshake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_work_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("last_license_status", sa.String(length=16), nullable=False, server_default=""),
        sa.Column(
            "updated_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "work_sso_codes",
        sa.Column("code_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_work_sso_codes_expires_at", "work_sso_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_work_sso_codes_expires_at", table_name="work_sso_codes")
    op.drop_table("work_sso_codes")
    op.drop_table("work_integrations")
