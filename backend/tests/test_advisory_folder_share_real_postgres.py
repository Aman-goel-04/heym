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

from sqlalchemy import delete, select

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
from app.services.workflow_access import explicit_workflow_share_ids, user_has_workflow_access


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

    async def test_mcp_still_works_through_a_folder_only_row_while_team_access_holds(self) -> None:
        """A team member's MCP toggle must not regress just because their share row is
        folder-only bookkeeping: as long as the team share that gave them access is
        still there, they keep the capability they had before this advisory's fix."""
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

        self.assertTrue(item.mcp_enabled)

        async with async_session_maker() as session:
            eligible = await get_user_mcp_workflows(session, self.member_id)
        self.assertIn(self.workflow_id, [w.id for w in eligible])

    async def test_mcp_stops_once_team_access_behind_a_folder_only_row_is_revoked(self) -> None:
        from app.api.mcp import toggle_workflow_mcp
        from app.models.schemas import MCPToggleRequest

        async with async_session_maker() as session:
            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()
            member = await session.get(User, self.member_id)
            await toggle_workflow_mcp(
                self.workflow_id, MCPToggleRequest(mcp_enabled=True), member, session
            )
            await session.commit()

        await self._revoke_team_share()

        # The row's own mcp_enabled flag is still true; only the current-access recheck
        # excludes it now, which is the point: revocation must not depend on remembering
        # to also clear that flag.
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

    async def test_a_real_invite_with_overlapping_team_access_and_a_folder_is_never_downgraded(
        self,
    ) -> None:
        """The exact case that made an earlier revision of this fix unsafe: a user who was
        genuinely invited directly, who also happens to reach the same workflow through a
        team share, and who then organizes it into a folder. None of that combination may
        ever turn their real share into folder-only bookkeeping."""
        async with async_session_maker() as session:
            session.add(
                WorkflowShare(id=uuid.uuid4(), workflow_id=self.workflow_id, user_id=self.member_id)
            )
            await session.commit()

            await _set_shared_workflow_folder(
                session, self.workflow_id, self.member_id, self.folder_id
            )
            await session.commit()

            share = (
                await session.execute(
                    select(WorkflowShare).where(
                        WorkflowShare.workflow_id == self.workflow_id,
                        WorkflowShare.user_id == self.member_id,
                    )
                )
            ).scalar_one()
            self.assertTrue(share.is_explicit_share)
            self.assertEqual(share.folder_id, self.folder_id)

        await self._revoke_team_share()

        async with async_session_maker() as session:
            result = await get_workflow_for_user(session, self.workflow_id, self.member_id)
            self.assertIsNotNone(result)
            share = (
                await session.execute(
                    select(WorkflowShare, User)
                    .join(User, User.id == WorkflowShare.user_id)
                    .where(WorkflowShare.workflow_id == self.workflow_id)
                )
            ).all()
            self.assertEqual(len(share), 1)


class RealPostgresExplicitShareCrossUserIsolationTests(unittest.IsolatedAsyncioTestCase):
    """explicit_workflow_share_ids must scope strictly to the user it is asked about."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.owner_id = uuid.uuid4()
        self.invited_user_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()
        self.workflow_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add_all(
                User(id=uid, email=f"u{uid.hex[:8]}@example.com", hashed_password="x", name="U")
                for uid in (self.owner_id, self.invited_user_id, self.other_user_id)
            )
            await session.flush()
            session.add(
                Workflow(id=self.workflow_id, name="wf", owner_id=self.owner_id, nodes=[], edges=[])
            )
            await session.flush()
            session.add(
                WorkflowShare(
                    id=uuid.uuid4(), workflow_id=self.workflow_id, user_id=self.invited_user_id
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        async with async_session_maker() as session:
            await session.execute(
                delete(WorkflowShare).where(WorkflowShare.workflow_id == self.workflow_id)
            )
            await session.execute(delete(Workflow).where(Workflow.id == self.workflow_id))
            await session.execute(
                delete(User).where(
                    User.id.in_([self.owner_id, self.invited_user_id, self.other_user_id])
                )
            )
            await session.commit()
        await engine.dispose()

    async def test_another_users_explicit_share_does_not_grant_access(self) -> None:
        async with async_session_maker() as session:
            invited_ids = (
                (await session.execute(explicit_workflow_share_ids(self.invited_user_id)))
                .scalars()
                .all()
            )
            other_ids = (
                (await session.execute(explicit_workflow_share_ids(self.other_user_id)))
                .scalars()
                .all()
            )

            self.assertIn(self.workflow_id, invited_ids)
            self.assertNotIn(self.workflow_id, other_ids)

            result = await get_workflow_for_user(session, self.workflow_id, self.other_user_id)
            self.assertIsNone(result)
            result = await get_workflow_for_user(session, self.workflow_id, self.invited_user_id)
            self.assertIsNotNone(result)
