"""Real-PostgreSQL coverage for GHSA-m42h-xrpg-h98v, added after review on PR #603.

The mocked tests in ``test_advisory_folder_share_revocation.py`` check the compiled SQL
shape of each fixed query. These tests run the actual code against a live database, so
they also catch anything a mock could hide: the MCP execution path, live execution
visibility, the data-remediation migration's effect on rows that already exist, and
that a real, legitimate direct share keeps working throughout.

Requires a reachable PostgreSQL instance with migrations applied, matching every other
``RealPostgres*`` test in this suite (see AGENTS.md, "PostgreSQL Integration Tests").
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, text

from app.api.folders import _accessible_workflow_filter, _set_shared_workflow_folder
from app.api.mcp import get_user_mcp_workflows
from app.api.workflows import get_workflow_for_user
from app.db.models import (
    Folder,
    Team,
    TeamMember,
    User,
    Workflow,
    WorkflowShare,
    WorkflowTeamShare,
)
from app.db.session import async_session_maker, engine
from app.services.execution_cancellation import list_persisted_active_executions_for_user
from app.services.workflow_access import user_has_workflow_access


class RealPostgresFolderShareRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.owner_id = uuid.uuid4()
        self.member_id = uuid.uuid4()
        self.team_id = uuid.uuid4()
        self.workflow_id = uuid.uuid4()
        self.folder_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add_all(
                [
                    User(
                        id=self.owner_id,
                        email=f"owner_{self.owner_id.hex[:8]}@example.com",
                        hashed_password="x",
                        name="Owner",
                    ),
                    User(
                        id=self.member_id,
                        email=f"member_{self.member_id.hex[:8]}@example.com",
                        hashed_password="x",
                        name="Member",
                    ),
                ]
            )
            await session.flush()
            session.add(Team(id=self.team_id, name="T", creator_id=self.owner_id))
            await session.flush()
            session.add(TeamMember(id=uuid.uuid4(), team_id=self.team_id, user_id=self.member_id))
            session.add(
                Workflow(
                    id=self.workflow_id,
                    name="Shared",
                    owner_id=self.owner_id,
                    nodes=[],
                    edges=[],
                )
            )
            session.add(
                Folder(
                    id=self.folder_id,
                    name="F",
                    owner_id=self.member_id,
                    parent_id=None,
                )
            )
            await session.flush()
            session.add(
                WorkflowTeamShare(
                    id=uuid.uuid4(), workflow_id=self.workflow_id, team_id=self.team_id
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        async with async_session_maker() as session:
            await session.execute(
                delete(WorkflowShare).where(WorkflowShare.workflow_id == self.workflow_id)
            )
            await session.execute(
                delete(WorkflowTeamShare).where(WorkflowTeamShare.workflow_id == self.workflow_id)
            )
            await session.execute(delete(TeamMember).where(TeamMember.team_id == self.team_id))
            await session.execute(delete(Team).where(Team.id == self.team_id))
            await session.execute(delete(Folder).where(Folder.id == self.folder_id))
            await session.execute(delete(Workflow).where(Workflow.id == self.workflow_id))
            await session.execute(delete(User).where(User.id.in_([self.owner_id, self.member_id])))
            await session.commit()
        await engine.dispose()

    async def _revoke_team_share(self) -> None:
        async with async_session_maker() as session:
            await session.execute(
                delete(WorkflowTeamShare).where(WorkflowTeamShare.workflow_id == self.workflow_id)
            )
            await session.commit()

    async def _remove_team_membership(self) -> None:
        async with async_session_maker() as session:
            await session.execute(
                delete(TeamMember).where(
                    TeamMember.team_id == self.team_id, TeamMember.user_id == self.member_id
                )
            )
            await session.commit()

    async def test_folder_placement_survives_neither_team_share_removal(self) -> None:
        """Filing into a folder, then losing the team share, must lose access - not keep it."""
        async with async_session_maker() as session:
            workflow = await session.get(Workflow, self.workflow_id)
            self.assertTrue(await user_has_workflow_access(session, workflow, self.member_id))

            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

        await self._revoke_team_share()

        async with async_session_maker() as session:
            workflow = await session.get(Workflow, self.workflow_id)
            self.assertFalse(await user_has_workflow_access(session, workflow, self.member_id))
            result = await get_workflow_for_user(session, self.workflow_id, self.member_id)
            self.assertIsNone(result)

    async def test_folder_placement_does_not_survive_team_membership_removal(self) -> None:
        async with async_session_maker() as session:
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

        await self._remove_team_membership()

        async with async_session_maker() as session:
            result = await get_workflow_for_user(session, self.workflow_id, self.member_id)
            self.assertIsNone(result)

    async def test_folders_own_access_filter_matches_the_canonical_clause(self) -> None:
        """folders.py's local _accessible_workflow_filter must not drift from workflow_access_clause."""
        async with async_session_maker() as session:
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

        await self._revoke_team_share()

        from sqlalchemy import select

        async with async_session_maker() as session:
            matched = (
                await session.execute(
                    select(Workflow.id).where(
                        Workflow.id == self.workflow_id,
                        _accessible_workflow_filter(self.member_id),
                    )
                )
            ).scalar_one_or_none()
            self.assertIsNone(matched)

    async def test_legitimate_direct_share_keeps_working_after_being_filed_into_a_folder(
        self,
    ) -> None:
        """A real invite is never affected by any of this, even once it is also organized."""
        async with async_session_maker() as session:
            session.add(
                WorkflowShare(id=uuid.uuid4(), workflow_id=self.workflow_id, user_id=self.member_id)
            )
            await session.commit()

        await self._revoke_team_share()
        await self._remove_team_membership()

        async with async_session_maker() as session:
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()
            result = await get_workflow_for_user(session, self.workflow_id, self.member_id)
            self.assertIsNotNone(result)

    async def test_mcp_cannot_be_enabled_through_a_folder_only_row(self) -> None:
        from app.api.mcp import toggle_workflow_mcp
        from app.models.schemas import MCPToggleRequest

        async with async_session_maker() as session:
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

            member = await session.get(User, self.member_id)
            item = await toggle_workflow_mcp(
                self.workflow_id, MCPToggleRequest(mcp_enabled=True), member, session
            )
            await session.commit()

        self.assertFalse(item.mcp_enabled)

        async with async_session_maker() as session:
            eligible = await get_user_mcp_workflows(session, self.member_id)
        self.assertNotIn(self.workflow_id, [w.id for w in eligible])

    async def test_mcp_toggle_works_for_a_real_direct_share(self) -> None:
        from app.api.mcp import toggle_workflow_mcp
        from app.models.schemas import MCPToggleRequest

        async with async_session_maker() as session:
            session.add(
                WorkflowShare(id=uuid.uuid4(), workflow_id=self.workflow_id, user_id=self.member_id)
            )
            await session.commit()

            member = await session.get(User, self.member_id)
            item = await toggle_workflow_mcp(
                self.workflow_id, MCPToggleRequest(mcp_enabled=True), member, session
            )
            await session.commit()

        self.assertTrue(item.mcp_enabled)

        async with async_session_maker() as session:
            eligible = await get_user_mcp_workflows(session, self.member_id)
        self.assertIn(self.workflow_id, [w.id for w in eligible])

    async def test_execution_data_is_not_visible_through_a_revoked_folder_only_row(self) -> None:
        from app.db.models import ActiveWorkflowExecution

        execution_id = uuid.uuid4()
        async with async_session_maker() as session:
            session.add(
                ActiveWorkflowExecution(
                    execution_id=execution_id,
                    workflow_id=self.workflow_id,
                    worker_id="test-worker",
                    started_at=datetime.now(timezone.utc),
                    heartbeat_at=datetime.now(timezone.utc),
                    inputs={},
                    running_node_ids=[],
                    node_results=[],
                )
            )
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

        await self._revoke_team_share()

        try:
            async with async_session_maker() as session:
                visible = await list_persisted_active_executions_for_user(session, self.member_id)
            self.assertNotIn(execution_id, [r.execution_id for r in visible])
        finally:
            async with async_session_maker() as session:
                await session.execute(
                    delete(ActiveWorkflowExecution).where(
                        ActiveWorkflowExecution.execution_id == execution_id
                    )
                )
                await session.commit()


class RealPostgresMigrationRemediationTests(unittest.IsolatedAsyncioTestCase):
    """Exercises revision 129's data backfill against the three shapes it must tell apart."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.owner_id = uuid.uuid4()
        self.with_team_id = uuid.uuid4()
        self.without_team_id = uuid.uuid4()
        self.explicit_id = uuid.uuid4()
        self.team_id = uuid.uuid4()
        self.folder_id = uuid.uuid4()
        self.wf_with_team = uuid.uuid4()
        self.wf_without_team = uuid.uuid4()
        self.wf_explicit = uuid.uuid4()

        async with async_session_maker() as session:
            session.add_all(
                User(id=uid, email=f"u{uid.hex[:8]}@example.com", hashed_password="x", name="U")
                for uid in (
                    self.owner_id,
                    self.with_team_id,
                    self.without_team_id,
                    self.explicit_id,
                )
            )
            await session.flush()
            session.add(Team(id=self.team_id, name="T", creator_id=self.owner_id))
            session.add(Folder(id=self.folder_id, name="F", owner_id=self.owner_id, parent_id=None))
            await session.flush()
            session.add(
                TeamMember(id=uuid.uuid4(), team_id=self.team_id, user_id=self.with_team_id)
            )
            session.add_all(
                Workflow(id=wid, name="wf", owner_id=self.owner_id, nodes=[], edges=[])
                for wid in (self.wf_with_team, self.wf_without_team, self.wf_explicit)
            )
            await session.flush()
            session.add(
                WorkflowTeamShare(
                    id=uuid.uuid4(), workflow_id=self.wf_with_team, team_id=self.team_id
                )
            )
            # All three pre-date the fix, so all start is_explicit_share=true, as the
            # 128 migration's default preserves for every row that already existed.
            session.add_all(
                [
                    WorkflowShare(
                        id=uuid.uuid4(),
                        workflow_id=self.wf_with_team,
                        user_id=self.with_team_id,
                        folder_id=self.folder_id,
                        is_explicit_share=True,
                    ),
                    WorkflowShare(
                        id=uuid.uuid4(),
                        workflow_id=self.wf_without_team,
                        user_id=self.without_team_id,
                        folder_id=self.folder_id,
                        is_explicit_share=True,
                    ),
                    WorkflowShare(
                        id=uuid.uuid4(),
                        workflow_id=self.wf_explicit,
                        user_id=self.explicit_id,
                        folder_id=self.folder_id,
                        is_explicit_share=True,
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        async with async_session_maker() as session:
            for wid in (self.wf_with_team, self.wf_without_team, self.wf_explicit):
                await session.execute(delete(WorkflowShare).where(WorkflowShare.workflow_id == wid))
                await session.execute(
                    delete(WorkflowTeamShare).where(WorkflowTeamShare.workflow_id == wid)
                )
                await session.execute(delete(Workflow).where(Workflow.id == wid))
            await session.execute(delete(TeamMember).where(TeamMember.team_id == self.team_id))
            await session.execute(delete(Team).where(Team.id == self.team_id))
            await session.execute(delete(Folder).where(Folder.id == self.folder_id))
            await session.execute(
                delete(User).where(
                    User.id.in_(
                        [self.owner_id, self.with_team_id, self.without_team_id, self.explicit_id]
                    )
                )
            )
            await session.commit()
        await engine.dispose()

    async def _run_remediation_sql(self) -> None:
        async with async_session_maker() as session:
            await session.execute(
                text(
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
            await session.commit()

    async def test_row_with_current_team_access_is_downgraded(self) -> None:
        await self._run_remediation_sql()
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT is_explicit_share FROM workflow_shares "
                        "WHERE workflow_id = :w AND user_id = :u"
                    ),
                    {"w": self.wf_with_team, "u": self.with_team_id},
                )
            ).scalar_one()
        self.assertFalse(row)

    async def test_ambiguous_row_without_team_access_is_left_alone(self) -> None:
        await self._run_remediation_sql()
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT is_explicit_share FROM workflow_shares "
                        "WHERE workflow_id = :w AND user_id = :u"
                    ),
                    {"w": self.wf_without_team, "u": self.without_team_id},
                )
            ).scalar_one()
        self.assertTrue(row)

    async def test_row_belonging_to_a_different_workflows_team_access_is_unaffected(self) -> None:
        """The explicit-share fixture happens to share the same folder id; confirms the
        remediation keys off (workflow_id, user_id) team access, not the folder alone."""
        await self._run_remediation_sql()
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT is_explicit_share FROM workflow_shares "
                        "WHERE workflow_id = :w AND user_id = :u"
                    ),
                    {"w": self.wf_explicit, "u": self.explicit_id},
                )
            ).scalar_one()
        self.assertTrue(row)


if __name__ == "__main__":
    unittest.main()
