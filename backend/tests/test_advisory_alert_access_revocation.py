"""heymrun/heym#659: an alert must not outlive its owner's access to the workflows it uses.

A workflow-scoped alert re-reads the watched workflow's execution metrics on every check, and
its notify workflow ran as that workflow's owner. So a collaborator who created an alert on a
shared workflow kept reading its metrics after the share was revoked, and could fire a shared
notify workflow on its owner's credentials, before or after revocation.
"""

import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api import alerts as alerts_api
from app.api.teams import remove_team_member
from app.api.workflows import remove_workflow_share, remove_workflow_team_share
from app.models.alert_schemas import AlertUpdate
from app.services.alerts import evaluator
from app.services.workflow_access import disable_alerts_without_access

ACCESS = "app.services.workflow_access.user_has_workflow_access"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _rows(items: list) -> SimpleNamespace:
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))


def _one(item: object) -> SimpleNamespace:
    return SimpleNamespace(scalar_one_or_none=lambda: item)


def _alert(owner_id: uuid.UUID, workflow_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), owner_id=owner_id, workflow_id=workflow_id, name="a", enabled=True
    )


def _db(results: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


class DisableAlertsWithoutAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_disables_only_alerts_whose_owner_lost_access(self) -> None:
        workflow = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
        removed, kept = uuid.uuid4(), uuid.uuid4()
        lost = [_alert(removed, workflow.id), _alert(removed, workflow.id)]
        still = _alert(kept, workflow.id)
        db = _db([_rows([*lost, still])])
        access = AsyncMock(side_effect=lambda _db, _wf, user_id: user_id == kept)

        with patch(ACCESS, access):
            disabled = await disable_alerts_without_access(db, workflow)

        self.assertEqual(disabled, lost)
        self.assertTrue(all(not alert.enabled for alert in lost))
        self.assertTrue(still.enabled)
        self.assertEqual(access.await_count, 2, "access is checked once per alert owner")

    async def test_query_targets_enabled_alerts_watching_the_workflow_not_owned_by_its_owner(
        self,
    ) -> None:
        workflow = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
        db = _db([_rows([])])

        await disable_alerts_without_access(db, workflow)

        sql = str(
            db.execute.await_args.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        where = sql.split("WHERE", 1)[1]
        self.assertIn("alerts.workflow_id =", where)
        self.assertIn("alerts.enabled IS true", where)
        self.assertIn("alerts.owner_id !=", where)
        self.assertNotIn("notify_workflow_id", where)


class ShareRemovalStopsAlertsTests(unittest.IsolatedAsyncioTestCase):
    def _setup(self) -> tuple[SimpleNamespace, SimpleNamespace, uuid.UUID]:
        owner = SimpleNamespace(id=uuid.uuid4())
        workflow = SimpleNamespace(id=uuid.uuid4(), owner_id=owner.id, name="wf")
        return owner, workflow, uuid.uuid4()

    async def test_user_share_removal_stops_alerts_by_default_and_audits_them(self) -> None:
        owner, workflow, removed = self._setup()
        alert = _alert(removed, workflow.id)
        db = _db([_one(workflow), _one(SimpleNamespace()), _rows([]), _rows([alert])])

        with (
            patch(ACCESS, AsyncMock(return_value=False)),
            patch("app.api.workflows.audit") as audit,
        ):
            await remove_workflow_share(workflow.id, user_id=removed, current_user=owner, db=db)

        self.assertFalse(alert.enabled)
        actions = [call.kwargs["action"] for call in audit.call_args_list]
        self.assertIn("alert.disable_on_access_revoke", actions)
        db.commit.assert_awaited_once()

    async def test_user_share_removal_can_keep_alerts_running(self) -> None:
        owner, workflow, removed = self._setup()
        db = _db([_one(workflow), _one(SimpleNamespace()), _rows([])])

        with patch(ACCESS, AsyncMock(return_value=False)):
            await remove_workflow_share(
                workflow.id, user_id=removed, stop_alerts=False, current_user=owner, db=db
            )

        self.assertEqual(db.execute.await_count, 3, "no alert query when the owner keeps them")
        db.commit.assert_awaited_once()

    async def test_team_share_removal_stops_alerts_unless_told_not_to(self) -> None:
        owner, workflow, removed = self._setup()
        alert = _alert(removed, workflow.id)
        db = _db([_one(workflow), _one(SimpleNamespace()), _rows([]), _rows([alert])])
        with patch(ACCESS, AsyncMock(return_value=False)):
            await remove_workflow_team_share(
                workflow.id, team_id=uuid.uuid4(), current_user=owner, db=db
            )
        self.assertFalse(alert.enabled)

        kept_db = _db([_one(workflow), _one(SimpleNamespace()), _rows([])])
        with patch(ACCESS, AsyncMock(return_value=False)):
            await remove_workflow_team_share(
                workflow.id, team_id=uuid.uuid4(), stop_alerts=False, current_user=owner, db=kept_db
            )
        self.assertEqual(kept_db.execute.await_count, 3)

    async def test_team_member_removal_always_stops_their_alerts(self) -> None:
        creator = SimpleNamespace(id=uuid.uuid4())
        team = SimpleNamespace(id=uuid.uuid4(), name="Team", creator_id=creator.id)
        removed = uuid.uuid4()
        workflow = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="wf")
        alert = _alert(removed, workflow.id)
        db = _db(
            [
                _one(team),
                _one(SimpleNamespace()),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [workflow.id])),
                _rows([workflow]),
                _rows([]),
                _rows([alert]),
            ]
        )

        with (
            patch(ACCESS, AsyncMock(return_value=False)),
            patch("app.api.teams.get_team", AsyncMock(return_value="ignored")),
        ):
            await remove_team_member(team.id, removed, db=db, current_user=creator)

        self.assertFalse(alert.enabled)


def _session_factory(*sessions: MagicMock):
    queue = list(sessions)

    @asynccontextmanager
    async def factory():
        yield queue.pop(0)

    return factory


class NotifyRunsAsTheAlertOwnerTests(unittest.IsolatedAsyncioTestCase):
    def _sessions(self, target: SimpleNamespace) -> tuple[MagicMock, MagicMock]:
        run_db = MagicMock()
        run_db.execute = AsyncMock(return_value=_one(target))
        run_db.add = MagicMock()
        run_db.flush = AsyncMock()
        run_db.commit = AsyncMock()
        record_db = MagicMock()
        record_db.execute = AsyncMock()
        record_db.commit = AsyncMock()
        return run_db, record_db

    async def test_notify_uses_the_alert_owners_credentials_not_the_workflow_owners(
        self,
    ) -> None:
        alert_owner = uuid.uuid4()
        target = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), nodes=[], edges=[])
        run_db, record_db = self._sessions(target)
        credentials = AsyncMock(return_value={})
        result = SimpleNamespace(
            outputs={}, node_results=[], status="success", execution_time_ms=1.0
        )
        execute = MagicMock(return_value=result)

        with (
            patch.object(evaluator, "async_session_maker", _session_factory(run_db, record_db)),
            patch.object(evaluator, "user_has_workflow_access", AsyncMock(return_value=True)),
            patch("app.api.workflows.collect_referenced_workflows", AsyncMock(return_value={})),
            patch("app.api.workflows.get_credentials_context", credentials),
            patch(
                "app.services.global_variables_service.get_global_variables_context",
                AsyncMock(return_value={}),
            ),
            patch("app.services.workflow_executor.execute_workflow", execute),
        ):
            await evaluator._run_notify_workflow(
                uuid.uuid4(), uuid.uuid4(), target.id, alert_owner, {}
            )

        credentials.assert_awaited_once_with(run_db, alert_owner)
        self.assertEqual(execute.call_args.kwargs["actor_user_id"], alert_owner)
        self.assertEqual(execute.call_args.kwargs["trace_user_id"], alert_owner)

    async def test_notify_is_skipped_once_the_alert_owner_loses_access(self) -> None:
        target = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), nodes=[], edges=[])
        run_db, record_db = self._sessions(target)
        execute = MagicMock()

        with (
            patch.object(evaluator, "async_session_maker", _session_factory(run_db, record_db)),
            patch.object(evaluator, "user_has_workflow_access", AsyncMock(return_value=False)),
            patch("app.services.workflow_executor.execute_workflow", execute),
        ):
            await evaluator._run_notify_workflow(
                uuid.uuid4(), uuid.uuid4(), target.id, uuid.uuid4(), {}
            )

        execute.assert_not_called()
        statement = record_db.execute.await_args.args[0]
        self.assertEqual(statement.compile().params["notify_status"], "skipped")

    async def test_dispatch_passes_the_alert_owner(self) -> None:
        alert = SimpleNamespace(
            id=uuid.uuid4(), owner_id=uuid.uuid4(), notify_workflow_id=uuid.uuid4()
        )
        runner = AsyncMock()
        with patch.object(evaluator, "_run_notify_workflow", runner):
            evaluator.dispatch_notify(alert, uuid.uuid4(), {})
            await next(iter(evaluator._notify_tasks))
        self.assertEqual(runner.await_args.args[3], alert.owner_id)


class ReenablingRechecksAccessTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, owner_id: uuid.UUID, **overrides) -> SimpleNamespace:
        values = {
            "id": uuid.uuid4(),
            "owner_id": owner_id,
            "name": "a",
            "description": None,
            "alert_type": "error_threshold",
            "scope": "workflow",
            "workflow_id": uuid.uuid4(),
            "config": {"window_minutes": 10, "threshold_count": 5},
            "enabled": False,
            "notify_workflow_id": None,
            "state": "ok",
            "renotify_mode": "on_recovery",
            "cooldown_minutes": None,
            "check_interval_seconds": 60,
            "last_evaluated_at": None,
            "last_triggered_at": None,
            "last_observed_value": None,
            "next_check_at": None,
            "created_at": None,
            "updated_at": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _db(self) -> MagicMock:
        db = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        rows = MagicMock()
        rows.all.return_value = []
        db.execute = AsyncMock(return_value=rows)
        return db

    async def test_reenabling_an_alert_on_a_revoked_workflow_explains_why(self) -> None:
        owner = SimpleNamespace(id=uuid.uuid4())
        row = self._row(owner.id)
        with (
            patch("app.api.alerts.get_owned_alert", AsyncMock(return_value=row)),
            patch("app.api.alerts.get_accessible_workflow_ids", AsyncMock(return_value=[])),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await alerts_api.update_alert(
                    row.id, AlertUpdate(enabled=True), db=self._db(), current_user=owner
                )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, alerts_api.RESUME_WITHOUT_ACCESS_DETAIL)
        self.assertFalse(row.enabled)

    async def test_testing_a_stopped_alert_rechecks_the_owners_access(self) -> None:
        owner_id = uuid.uuid4()
        viewer = SimpleNamespace(id=uuid.uuid4())
        row = self._row(owner_id)
        accessible = AsyncMock(return_value=[])
        observe = AsyncMock()
        with (
            patch("app.api.alerts.get_accessible_alert", AsyncMock(return_value=row)),
            patch("app.api.alerts.get_accessible_workflow_ids", accessible),
            patch("app.api.alerts.observe", observe),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await alerts_api.test_alert(row.id, db=self._db(), current_user=viewer)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(accessible.await_args.args[1], owner_id)
        observe.assert_not_awaited()

    async def test_testing_an_enabled_alert_does_not_recheck_access(self) -> None:
        owner = SimpleNamespace(id=uuid.uuid4())
        row = self._row(owner.id, enabled=True)
        accessible = AsyncMock(return_value=[])
        observation = SimpleNamespace(
            observed_value=1.0, threshold_value=5.0, breached=False, context={}
        )
        with (
            patch("app.api.alerts.get_accessible_alert", AsyncMock(return_value=row)),
            patch("app.api.alerts.get_accessible_workflow_ids", accessible),
            patch(
                "app.api.alerts.observe",
                AsyncMock(return_value=(observation, NOW, NOW)),
            ),
        ):
            response = await alerts_api.test_alert(row.id, db=self._db(), current_user=owner)
        accessible.assert_not_awaited()
        self.assertEqual(response.observed_value, 1.0)

    async def test_editing_a_kept_alert_does_not_recheck_access(self) -> None:
        """A workflow owner may revoke a share and keep the collaborator's alerts running."""
        owner = SimpleNamespace(id=uuid.uuid4())
        row = self._row(owner.id, enabled=True)
        accessible = AsyncMock(return_value=[])
        with (
            patch("app.api.alerts.get_owned_alert", AsyncMock(return_value=row)),
            patch("app.api.alerts.get_accessible_workflow_ids", accessible),
            patch("app.api.alerts.audit"),
        ):
            await alerts_api.update_alert(
                row.id, AlertUpdate(name="renamed"), db=self._db(), current_user=owner
            )
        accessible.assert_not_awaited()
        self.assertEqual(row.name, "renamed")


if __name__ == "__main__":
    unittest.main()
