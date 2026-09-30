"""PostgreSQL regression test: MCP config eligibility for a folder-only team share row.

A team member who files a team-shared workflow into a personal folder gets a
``WorkflowShare`` row with ``is_explicit_share=False``. That row is correctly excluded
from anything that means "a real, owner-granted direct share" (``explicit_workflow_share_ids``),
but ``get_all_user_workflows`` (the MCP settings page listing) must still show the
workflow while the underlying team access holds, since the member can enable and run it
through MCP regardless. Revoking that team access - by removing the ``WorkflowTeamShare``
or by removing the member from the team - must drop it from the listing immediately.
"""

import unittest
import uuid

from app.api.mcp import get_all_user_workflows
from app.db.models import Team, TeamMember, User, Workflow, WorkflowShare, WorkflowTeamShare
from app.db.session import async_session_maker, engine


class MCPConfigFolderOnlyTeamShareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()

        self.owner_id = uuid.uuid4()
        self.member_id = uuid.uuid4()
        self.team_id = uuid.uuid4()
        self.wf_id = uuid.uuid4()
        self.folder_id = uuid.uuid4()

        async with async_session_maker() as session:
            from app.db.models import Folder

            owner = User(
                id=self.owner_id,
                email=f"owner_{self.owner_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Workflow Owner",
            )
            member = User(
                id=self.member_id,
                email=f"member_{self.member_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Team Member",
            )
            session.add_all([owner, member])
            await session.flush()

            team = Team(id=self.team_id, name="Eng Team", creator_id=self.owner_id)
            session.add(team)
            await session.flush()

            session.add(TeamMember(id=uuid.uuid4(), team_id=self.team_id, user_id=self.member_id))

            wf = Workflow(
                id=self.wf_id,
                name="Team Shared Workflow",
                owner_id=self.owner_id,
                nodes=[{"id": "node-1", "data": {"label": "Start"}}],
                edges=[],
            )
            session.add(wf)
            await session.flush()

            session.add(
                WorkflowTeamShare(id=uuid.uuid4(), workflow_id=self.wf_id, team_id=self.team_id)
            )

            folder = Folder(id=self.folder_id, owner_id=self.member_id, name="My Folder")
            session.add(folder)
            await session.flush()

            # Folder-only placement row: created by filing the team-shared workflow into a
            # personal folder, not by an owner-granted direct share.
            session.add(
                WorkflowShare(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    user_id=self.member_id,
                    folder_id=self.folder_id,
                    is_explicit_share=False,
                    mcp_enabled=False,
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        from sqlalchemy import delete

        from app.db.models import Folder

        async with async_session_maker() as session:
            await session.execute(
                delete(WorkflowTeamShare).where(WorkflowTeamShare.workflow_id == self.wf_id)
            )
            await session.execute(
                delete(WorkflowShare).where(WorkflowShare.workflow_id == self.wf_id)
            )
            await session.execute(delete(Workflow).where(Workflow.id == self.wf_id))
            await session.execute(delete(Folder).where(Folder.id == self.folder_id))
            await session.execute(delete(TeamMember).where(TeamMember.team_id == self.team_id))
            await session.execute(delete(Team).where(Team.id == self.team_id))
            await session.execute(delete(User).where(User.id.in_([self.owner_id, self.member_id])))
            await session.commit()
        await engine.dispose()

    async def test_folder_only_row_still_appears_while_team_access_holds(self) -> None:
        """The workflow is listed even though its only share row is a folder placement, and
        even though MCP is currently disabled on that row."""
        async with async_session_maker() as session:
            workflows = await get_all_user_workflows(session, self.member_id)
        self.assertIn(self.wf_id, {w.id for w in workflows})

    async def test_disappears_after_team_share_removal(self) -> None:
        from sqlalchemy import delete

        async with async_session_maker() as session:
            await session.execute(
                delete(WorkflowTeamShare).where(WorkflowTeamShare.workflow_id == self.wf_id)
            )
            await session.commit()

        async with async_session_maker() as session:
            workflows = await get_all_user_workflows(session, self.member_id)
        self.assertNotIn(self.wf_id, {w.id for w in workflows})

    async def test_disappears_after_team_membership_removal(self) -> None:
        from sqlalchemy import delete

        async with async_session_maker() as session:
            await session.execute(
                delete(TeamMember).where(
                    TeamMember.team_id == self.team_id, TeamMember.user_id == self.member_id
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            workflows = await get_all_user_workflows(session, self.member_id)
        self.assertNotIn(self.wf_id, {w.id for w in workflows})

    async def test_unrelated_user_never_sees_it(self) -> None:
        unrelated_id = uuid.uuid4()
        async with async_session_maker() as session:
            unrelated = User(
                id=unrelated_id,
                email=f"unrelated_{unrelated_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Unrelated",
            )
            session.add(unrelated)
            await session.commit()
        try:
            async with async_session_maker() as session:
                workflows = await get_all_user_workflows(session, unrelated_id)
            self.assertNotIn(self.wf_id, {w.id for w in workflows})
        finally:
            from sqlalchemy import delete

            async with async_session_maker() as session:
                await session.execute(delete(User).where(User.id == unrelated_id))
                await session.commit()


if __name__ == "__main__":
    unittest.main()
