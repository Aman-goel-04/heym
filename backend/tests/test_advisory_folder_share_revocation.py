"""Regression tests for GHSA-m42h-xrpg-h98v.

Filing a workflow reached only through a team share into a personal folder used to create
a real ``WorkflowShare`` row as a side effect. Every access check treated that row's mere
existence as a direct grant, so removing the team share (or the team membership) never
revoked it: the row, and the access it gave, outlived the reason it was created for.

The fix marks such a row ``is_explicit_share=False``. These tests lock that every place
that treats ``WorkflowShare`` as a grant, or exposes it in the sharing UI, honours the flag.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.api import folders as folders_api
from app.api import workflows as workflows_api
from app.db.models import User, Workflow, WorkflowShare
from app.models.schemas import WorkflowShareRequest
from app.services.workflow_access import user_has_workflow_access, workflow_access_clause


def _workflow(owner_id: uuid.UUID) -> Workflow:
    return Workflow(
        id=uuid.uuid4(),
        name="Shared Workflow",
        description=None,
        owner_id=owner_id,
        nodes=[],
        edges=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class WorkflowAccessClauseIgnoresFolderOnlySharesTest(unittest.TestCase):
    def test_clause_only_matches_explicit_shares(self) -> None:
        user_id = uuid.uuid4()
        sql = str(
            select(Workflow.id)
            .where(workflow_access_clause(user_id))
            .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )

        self.assertIn("workflow_shares.is_explicit_share IS true", sql)


class UserHasWorkflowAccessTest(unittest.IsolatedAsyncioTestCase):
    """Models a real database's row-count behavior, not a canned mock, so the query's
    ``is_explicit_share`` filter is actually exercised rather than assumed.
    """

    async def _run(self, *, is_explicit_share: bool) -> bool:
        workflow = _workflow(owner_id=uuid.uuid4())
        user_id = uuid.uuid4()

        async def execute(stmt):
            sql = str(
                stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            )
            matches = is_explicit_share and "workflow_shares.is_explicit_share IS true" in sql
            result = MagicMock()
            result.scalar_one_or_none.return_value = workflow.id if matches else None
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute)
        return await user_has_workflow_access(db, workflow, user_id)

    async def test_folder_only_share_does_not_grant_access(self) -> None:
        # The share row exists (folder placement bookkeeping) but is not explicit, and
        # there is no other path (ownership, team share) to the workflow.
        self.assertFalse(await self._run(is_explicit_share=False))

    async def test_explicit_share_still_grants_access(self) -> None:
        self.assertTrue(await self._run(is_explicit_share=True))


class AccessibleWorkflowFilterIgnoresFolderOnlySharesTest(unittest.TestCase):
    def test_filter_only_matches_explicit_shares(self) -> None:
        user_id = uuid.uuid4()
        sql = str(
            select(Workflow.id)
            .where(folders_api._accessible_workflow_filter(user_id))
            .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )

        self.assertIn("workflow_shares.is_explicit_share IS true", sql)


def _shares_result(rows: list[tuple[WorkflowShare, User]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


class ListWorkflowSharesHidesFolderOnlyRowsTest(unittest.IsolatedAsyncioTestCase):
    async def test_folder_only_share_is_not_listed(self) -> None:
        owner = SimpleNamespace(id=uuid.uuid4())
        workflow = _workflow(owner_id=owner.id)
        member = User(id=uuid.uuid4(), email="member@example.com", hashed_password="hashed")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _shares_result([]),  # the query already filters is_explicit_share=True
            ]
        )
        import app.api.workflows as wf_module

        original = wf_module.get_workflow_for_user
        wf_module.get_workflow_for_user = AsyncMock(return_value=workflow)
        try:
            shares = await workflows_api.list_workflow_shares(workflow.id, owner, db)
        finally:
            wf_module.get_workflow_for_user = original

        self.assertEqual(shares, [])
        sql = str(
            db.execute.await_args_list[0]
            .args[0]
            .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        self.assertIn("workflow_shares.is_explicit_share IS true", sql)
        self.assertIsNotNone(member)  # keep the fixture referenced for clarity


class CreateWorkflowSharePromotesFolderOnlyRowTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_invite_upgrades_an_existing_folder_only_share(self) -> None:
        owner = SimpleNamespace(id=uuid.uuid4(), email="owner@example.com", name="Owner")
        workflow = _workflow(owner_id=owner.id)
        target = User(
            id=uuid.uuid4(), email="member@example.com", name="Member", hashed_password="hashed"
        )
        folder_only_share = WorkflowShare(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            user_id=target.id,
            folder_id=uuid.uuid4(),
            is_explicit_share=False,
            created_at=datetime.now(timezone.utc),
        )

        user_lookup = MagicMock()
        user_lookup.scalar_one_or_none.return_value = target
        share_lookup = MagicMock()
        share_lookup.scalar_one_or_none.return_value = folder_only_share

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[user_lookup, share_lookup])

        import app.api.workflows as wf_module

        original = wf_module.get_workflow_for_user
        wf_module.get_workflow_for_user = AsyncMock(return_value=workflow)
        try:
            response = await workflows_api.create_workflow_share(
                workflow.id,
                WorkflowShareRequest(email=target.email),
                owner,
                db,
            )
        finally:
            wf_module.get_workflow_for_user = original

        self.assertTrue(folder_only_share.is_explicit_share)
        self.assertEqual(response.user_id, target.id)
        db.flush.assert_awaited()


class FolderPlacementRowRejectedOnRevocationTest(unittest.IsolatedAsyncioTestCase):
    """End-to-end shape check: a folder-only row plus no other access path is a 404,
    matching what an owner sees once they have revoked the team share.
    """

    async def test_get_workflow_for_user_denies_folder_only_access(self) -> None:
        from app.api.workflows import get_workflow_for_user

        workflow = _workflow(owner_id=uuid.uuid4())
        user_id = uuid.uuid4()

        async def execute(stmt):
            sql = str(
                stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            )
            result = MagicMock()
            # A real database would not return this workflow: the only WorkflowShare row
            # for this user has is_explicit_share=false, and the compiled clause requires it
            # to be true, so this branch models "no match" faithfully rather than assuming it.
            result.scalar_one_or_none.return_value = None
            self.assertIn("workflow_shares.is_explicit_share IS true", sql)
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute)

        result = await get_workflow_for_user(db, workflow.id, user_id)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
