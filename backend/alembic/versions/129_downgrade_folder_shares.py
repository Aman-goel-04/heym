"""Best-effort remediation for GHSA-m42h-xrpg-h98v on existing data.

The previous revision stops new rows from being created as standing grants, but it
defaults every already-existing ``workflow_shares`` row to ``is_explicit_share=true``
so no currently-legitimate direct share is silently downgraded. That default also
preserves the access the bug itself already granted on any instance that hit it
before upgrading, which this revision narrows without risking a real share.

There is no reliable, fully general way to tell a bug-created row apart from a
real direct share after the fact: both can carry a ``folder_id``, and Heym's audit
trail is stdout-only (see AGENTS.md, "Audit logging stays on the main instance"),
never written to a queryable table. This migration only handles the one case that
can be fixed with zero risk: a row where the same user also currently reaches the
same workflow through a team share. Downgrading that row to ``is_explicit_share
=false`` cannot remove any access the user does not already have today (the team
share still grants it), and it closes exactly the future risk the advisory
describes - if that team share is later removed, the row no longer survives it.

A row with no such team share right now cannot be resolved by this migration
without a chance of revoking a real, intentional share. Operators who believe
they were affected before upgrading should review ``workflow_shares`` rows with
a non-null ``folder_id`` against who they actually invited.

Revision ID: 129_downgrade_folder_shares
Revises: 128_workflow_share_explicit_flag
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "129_downgrade_folder_shares"
down_revision: Union[str, None] = "128_workflow_share_explicit_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE workflow_shares
            SET is_explicit_share = false
            WHERE folder_id IS NOT NULL
              AND is_explicit_share = true
              AND EXISTS (
                  SELECT 1
                  FROM workflow_team_shares wts
                  JOIN team_members tm ON tm.team_id = wts.team_id
                  WHERE wts.workflow_id = workflow_shares.workflow_id
                    AND tm.user_id = workflow_shares.user_id
              )
            """
        )
    )


def downgrade() -> None:
    # Intentionally a no-op: which rows this touched is not recorded, and the
    # rows it touched still grant access today through the team share that
    # justified downgrading them, so there is nothing to restore.
    pass
