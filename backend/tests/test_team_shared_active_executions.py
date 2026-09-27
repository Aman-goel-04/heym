"""PostgreSQL regression tests for team-shared workflow executions.

Guards the active-execution and recent-execution discovery queries:
1. Team access via WorkflowTeamShare for queued, persisted, pending-review, and local handles.
2. Multiple teams / direct-share overlap without duplicate rows.
3. Unrelated user rejection.
4. Revocation after WorkflowTeamShare removal or TeamMember removal.
5. Dashboard widget workflows: writable shared dashboards grant workflow visibility;
   read-only dashboard access alone never grants workflow visibility.
6. Preservation of cancellation, staleness, expiry, and history filters.
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.api.workflows import get_recent_executions_for_user, list_active_workflow_executions
from app.db.models import (
    ActiveWorkflowExecution,
    CodexFollowupRequest,
    Dashboard,
    DashboardShare,
    DashboardTeamShare,
    DashboardWidget,
    ExecutionHistory,
    HITLRequest,
    Team,
    TeamMember,
    User,
    Workflow,
    WorkflowRunQueue,
    WorkflowShare,
    WorkflowTeamShare,
)
from app.db.session import async_session_maker, engine
from app.services.active_execution_overview import (
    build_active_execution_overview,
    collect_active_executions_for_user,
)
from app.services.cluster.run_queue import STATUS_QUEUED
from app.services.execution_cancellation import (
    clear_execution,
    list_pending_review_executions_for_user,
    list_persisted_active_executions_for_user,
    register_execution,
)


class TeamSharedExecutionsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()

        self.owner_id = uuid.uuid4()
        self.member_id = uuid.uuid4()
        self.unrelated_id = uuid.uuid4()
        self.team1_id = uuid.uuid4()
        self.team2_id = uuid.uuid4()
        self.wf_id = uuid.uuid4()

        self.users_to_clean = [self.owner_id, self.member_id, self.unrelated_id]
        self.teams_to_clean = [self.team1_id, self.team2_id]
        self.workflows_to_clean = [self.wf_id]
        self.dashboards_to_clean: list[uuid.UUID] = []
        self.handles_to_deregister: list[tuple[uuid.UUID, uuid.UUID]] = []

        async with async_session_maker() as session:
            # Users
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
            unrelated = User(
                id=self.unrelated_id,
                email=f"unrelated_{self.unrelated_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Unrelated User",
            )
            session.add_all([owner, member, unrelated])
            await session.flush()

            # Teams
            team1 = Team(
                id=self.team1_id,
                name="Engineering Team 1",
                creator_id=self.owner_id,
            )
            team2 = Team(
                id=self.team2_id,
                name="Engineering Team 2",
                creator_id=self.owner_id,
            )
            session.add_all([team1, team2])
            await session.flush()

            # Membership: member is in both teams
            tm1 = TeamMember(id=uuid.uuid4(), team_id=self.team1_id, user_id=self.member_id)
            tm2 = TeamMember(id=uuid.uuid4(), team_id=self.team2_id, user_id=self.member_id)
            session.add_all([tm1, tm2])
            await session.flush()

            # Workflow owned by owner
            wf = Workflow(
                id=self.wf_id,
                name="Shared Team Pipeline",
                owner_id=self.owner_id,
                nodes=[{"id": "node-1", "data": {"label": "Start Step"}}],
                edges=[],
            )
            session.add(wf)
            await session.flush()

            # Share workflow with team 1
            ts1 = WorkflowTeamShare(id=uuid.uuid4(), workflow_id=self.wf_id, team_id=self.team1_id)
            session.add(ts1)
            await session.commit()

    async def asyncTearDown(self) -> None:
        for ex_id, _ in self.handles_to_deregister:
            clear_execution(ex_id)

        from sqlalchemy import delete

        async with async_session_maker() as session:
            if self.workflows_to_clean:
                await session.execute(
                    delete(ActiveWorkflowExecution).where(
                        ActiveWorkflowExecution.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(WorkflowRunQueue).where(
                        WorkflowRunQueue.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(HITLRequest).where(HITLRequest.workflow_id.in_(self.workflows_to_clean))
                )
                await session.execute(
                    delete(CodexFollowupRequest).where(
                        CodexFollowupRequest.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(ExecutionHistory).where(
                        ExecutionHistory.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(WorkflowTeamShare).where(
                        WorkflowTeamShare.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(WorkflowShare).where(
                        WorkflowShare.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardWidget).where(
                        DashboardWidget.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(Workflow).where(Workflow.id.in_(self.workflows_to_clean))
                )

            if self.dashboards_to_clean:
                await session.execute(
                    delete(DashboardShare).where(
                        DashboardShare.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardTeamShare).where(
                        DashboardTeamShare.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardWidget).where(
                        DashboardWidget.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(Dashboard).where(Dashboard.id.in_(self.dashboards_to_clean))
                )

            if self.teams_to_clean:
                await session.execute(
                    delete(TeamMember).where(TeamMember.team_id.in_(self.teams_to_clean))
                )
                await session.execute(delete(Team).where(Team.id.in_(self.teams_to_clean)))

            if self.users_to_clean:
                await session.execute(delete(User).where(User.id.in_(self.users_to_clean)))
            await session.commit()
        await engine.dispose()

    # =========================================================================
    # 1. Team access for active executions: persisted, queued, pending reviews
    # =========================================================================

    async def test_team_member_sees_persisted_and_queued_active_executions(self) -> None:
        """A team member can list active persisted and queued executions for team-shared workflows."""
        now = datetime.now(timezone.utc)
        ex_persisted_id = uuid.uuid4()
        ex_queued_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_persisted_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-1",
                    started_at=now,
                    heartbeat_at=now,
                    inputs={"key": "active"},
                )
            )
            session.add(
                WorkflowRunQueue(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    execution_id=ex_queued_id,
                    placement="anywhere",
                    target_instance_id="worker-1",
                    status=STATUS_QUEUED,
                    inputs={"key": "queued"},
                    trigger_source="api",
                    actor_user_id=self.owner_id,
                    credentials_owner_id=self.owner_id,
                    test_run=False,
                    timeout_seconds=60.0,
                    return_on_chart_output=False,
                    enqueued_at=now,
                    not_after=now + timedelta(seconds=120),
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            # 1. list_persisted_active_executions_for_user returns both for team member
            records = await list_persisted_active_executions_for_user(session, self.member_id)
            rec_ids = {r.execution_id for r in records}
            self.assertIn(ex_persisted_id, rec_ids)
            self.assertIn(ex_queued_id, rec_ids)

            # 2. collect_active_executions_for_user returns both for team member
            items = await collect_active_executions_for_user(session, self.member_id)
            item_ids = {uuid.UUID(it.execution_id) for it in items}
            self.assertIn(ex_persisted_id, item_ids)
            self.assertIn(ex_queued_id, item_ids)

            # 2b. list_active_workflow_executions API route returns both for team member
            member_user = await session.get(User, self.member_id)
            api_items = await list_active_workflow_executions(current_user=member_user, db=session)
            api_item_ids = {uuid.UUID(it.execution_id) for it in api_items}
            self.assertIn(ex_persisted_id, api_item_ids)
            self.assertIn(ex_queued_id, api_item_ids)

            # 3. build_active_execution_overview includes them
            overview = await build_active_execution_overview(session, self.member_id)
            overview_ids = {uuid.UUID(e["execution_id"]) for e in overview["executions"]}
            self.assertIn(ex_persisted_id, overview_ids)
            self.assertIn(ex_queued_id, overview_ids)

            # 4. Unrelated user sees neither
            unrelated_records = await list_persisted_active_executions_for_user(
                session, self.unrelated_id
            )
            unrelated_ids = {r.execution_id for r in unrelated_records}
            self.assertNotIn(ex_persisted_id, unrelated_ids)
            self.assertNotIn(ex_queued_id, unrelated_ids)

    async def test_team_member_sees_pending_hitl_and_codex_reviews(self) -> None:
        """A team member can list pending HITL and Codex reviews for team-shared workflows."""
        now = datetime.now(timezone.utc)
        hitl_hist_id = uuid.uuid4()
        codex_hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add_all(
                [
                    ExecutionHistory(
                        id=hitl_hist_id,
                        workflow_id=self.wf_id,
                        started_at=now - timedelta(minutes=5),
                        status="pending_review",
                    ),
                    ExecutionHistory(
                        id=codex_hist_id,
                        workflow_id=self.wf_id,
                        started_at=now - timedelta(minutes=2),
                        status="pending_review",
                    ),
                ]
            )
            await session.flush()

            session.add(
                HITLRequest(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    execution_history_id=hitl_hist_id,
                    public_token=f"tok-hitl-{uuid.uuid4().hex[:8]}",
                    workflow_name="Shared Team Pipeline",
                    agent_node_id="agent-node-1",
                    agent_label="Approval Step",
                    summary="Need user confirmation",
                    status="pending",
                    expires_at=now + timedelta(hours=1),
                )
            )
            session.add(
                CodexFollowupRequest(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    execution_history_id=codex_hist_id,
                    public_token=f"tok-codex-{uuid.uuid4().hex[:8]}",
                    workflow_name="Shared Team Pipeline",
                    codex_node_id="codex-node-1",
                    codex_label="Codex Followup Step",
                    summary="Need code review confirmation",
                    status="pending",
                    expires_at=now + timedelta(hours=1),
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            reviews = await list_pending_review_executions_for_user(session, self.member_id)
            review_ids = {r.execution_id for r in reviews}
            self.assertIn(hitl_hist_id, review_ids)
            self.assertIn(codex_hist_id, review_ids)

            items = await collect_active_executions_for_user(session, self.member_id)
            item_map = {uuid.UUID(it.execution_id): it for it in items}
            self.assertIn(hitl_hist_id, item_map)
            self.assertIn(codex_hist_id, item_map)
            self.assertEqual(item_map[hitl_hist_id].status, "pending")
            self.assertEqual(item_map[hitl_hist_id].pending_kind, "hitl")
            self.assertEqual(item_map[codex_hist_id].status, "pending")
            self.assertEqual(item_map[codex_hist_id].pending_kind, "codex")

            # Unrelated user sees neither
            unrelated_reviews = await list_pending_review_executions_for_user(
                session, self.unrelated_id
            )
            unrelated_ids = {r.execution_id for r in unrelated_reviews}
            self.assertNotIn(hitl_hist_id, unrelated_ids)
            self.assertNotIn(codex_hist_id, unrelated_ids)

    async def test_team_member_sees_local_handle_execution(self) -> None:
        """A local running handle on a team-shared workflow is discovered via collect_active_executions_for_user."""
        ex_local_id = uuid.uuid4()
        register_execution(
            workflow_id=self.wf_id,
            execution_id=ex_local_id,
            inputs={"local": "handle"},
        )
        self.handles_to_deregister.append((ex_local_id, self.wf_id))

        async with async_session_maker() as session:
            items = await collect_active_executions_for_user(session, self.member_id)
            item_ids = {uuid.UUID(it.execution_id) for it in items}
            self.assertIn(ex_local_id, item_ids)

            # Unrelated user cannot see it
            unrelated_items = await collect_active_executions_for_user(session, self.unrelated_id)
            unrelated_ids = {uuid.UUID(it.execution_id) for it in unrelated_items}
            self.assertNotIn(ex_local_id, unrelated_ids)

    # =========================================================================
    # 2. Multiple teams & share overlap without duplicates
    # =========================================================================

    async def test_multiple_teams_and_direct_share_do_not_produce_duplicates(self) -> None:
        """A user in multiple shared teams (and direct share) gets exactly one copy of each execution."""
        now = datetime.now(timezone.utc)
        ex_id = uuid.uuid4()
        hitl_id = uuid.uuid4()

        async with async_session_maker() as session:
            # Share with team 2 as well
            session.add(
                WorkflowTeamShare(id=uuid.uuid4(), workflow_id=self.wf_id, team_id=self.team2_id)
            )
            # Add direct WorkflowShare as well (full overlap)
            session.add(
                WorkflowShare(id=uuid.uuid4(), workflow_id=self.wf_id, user_id=self.member_id)
            )
            # Active execution
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-multi",
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            # Pending review
            session.add(
                ExecutionHistory(
                    id=hitl_id,
                    workflow_id=self.wf_id,
                    started_at=now,
                    status="pending_review",
                )
            )
            await session.flush()
            session.add(
                HITLRequest(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    execution_history_id=hitl_id,
                    public_token=f"tok-multi-{uuid.uuid4().hex[:8]}",
                    workflow_name="Shared Team Pipeline",
                    agent_node_id="node-multi",
                    agent_label="Approval",
                    summary="Test multi-team deduplication",
                    status="pending",
                    expires_at=now + timedelta(hours=1),
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records = await list_persisted_active_executions_for_user(session, self.member_id)
            matching_records = [r for r in records if r.execution_id == ex_id]
            self.assertEqual(len(matching_records), 1)

            reviews = await list_pending_review_executions_for_user(session, self.member_id)
            matching_reviews = [r for r in reviews if r.execution_id == hitl_id]
            self.assertEqual(len(matching_reviews), 1)

            items = await collect_active_executions_for_user(session, self.member_id)
            matching_items_ex = [it for it in items if it.execution_id == str(ex_id)]
            matching_items_hitl = [it for it in items if it.execution_id == str(hitl_id)]
            self.assertEqual(len(matching_items_ex), 1)
            self.assertEqual(len(matching_items_hitl), 1)

    # =========================================================================
    # 3. Access revocation: share deletion & membership removal
    # =========================================================================

    async def test_active_executions_revocation_on_share_removal(self) -> None:
        """Removing WorkflowTeamShare immediately revokes active execution visibility for team members."""
        from sqlalchemy import delete

        now = datetime.now(timezone.utc)
        ex_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-rev",
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records = await list_persisted_active_executions_for_user(session, self.member_id)
            self.assertTrue(any(r.execution_id == ex_id for r in records))

            # Delete the team share
            await session.execute(
                delete(WorkflowTeamShare).where(
                    WorkflowTeamShare.workflow_id == self.wf_id,
                    WorkflowTeamShare.team_id == self.team1_id,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records_after = await list_persisted_active_executions_for_user(session, self.member_id)
            self.assertFalse(any(r.execution_id == ex_id for r in records_after))

    async def test_active_executions_revocation_on_team_membership_removal(self) -> None:
        """Removing TeamMember immediately revokes active execution visibility."""
        from sqlalchemy import delete

        now = datetime.now(timezone.utc)
        ex_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-rev-mem",
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records = await list_persisted_active_executions_for_user(session, self.member_id)
            self.assertTrue(any(r.execution_id == ex_id for r in records))

            # Remove member from team 1
            await session.execute(
                delete(TeamMember).where(
                    TeamMember.team_id == self.team1_id,
                    TeamMember.user_id == self.member_id,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records_after = await list_persisted_active_executions_for_user(session, self.member_id)
            self.assertFalse(any(r.execution_id == ex_id for r in records_after))

    # =========================================================================
    # 4. Dashboard widget workflows: write vs read-only access
    # =========================================================================

    async def test_dashboard_widget_active_executions_write_vs_read_access(self) -> None:
        """Write collaborators on a dashboard can see its widget workflow active executions; read-only cannot."""
        now = datetime.now(timezone.utc)
        widget_wf_id = uuid.uuid4()
        dashboard_id = uuid.uuid4()
        reader_id = uuid.uuid4()
        writer_id = uuid.uuid4()

        self.workflows_to_clean.append(widget_wf_id)
        self.dashboards_to_clean.append(dashboard_id)
        self.users_to_clean.extend([reader_id, writer_id])

        ex_widget_id = uuid.uuid4()

        async with async_session_maker() as session:
            reader = User(
                id=reader_id,
                email=f"reader_{reader_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Dashboard Reader",
            )
            writer = User(
                id=writer_id,
                email=f"writer_{writer_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Dashboard Writer",
            )
            session.add_all([reader, writer])
            await session.flush()

            # Dashboard owned by owner
            dash = Dashboard(
                id=dashboard_id,
                name="Analytics Dashboard",
                owner_id=self.owner_id,
            )
            session.add(dash)
            await session.flush()

            # Widget workflow (kind="dashboard_widget")
            widget_wf = Workflow(
                id=widget_wf_id,
                name="Widget Hidden Workflow",
                kind="dashboard_widget",
                owner_id=self.owner_id,
                nodes=[],
                edges=[],
            )
            session.add(widget_wf)
            await session.flush()

            # Dashboard widget
            widget = DashboardWidget(
                id=uuid.uuid4(),
                dashboard_id=dashboard_id,
                workflow_id=widget_wf_id,
                title="Active Chart Widget",
            )
            session.add(widget)

            # Dashboard shares: reader has "read", writer has "write"
            session.add(
                DashboardShare(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard_id,
                    user_id=reader_id,
                    permission="read",
                )
            )
            session.add(
                DashboardShare(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard_id,
                    user_id=writer_id,
                    permission="write",
                )
            )

            # Active execution on the widget workflow
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_widget_id,
                    workflow_id=widget_wf_id,
                    worker_id="worker-widget",
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            # Writer sees it
            writer_records = await list_persisted_active_executions_for_user(session, writer_id)
            writer_ids = {r.execution_id for r in writer_records}
            self.assertIn(ex_widget_id, writer_ids)

            writer_items = await collect_active_executions_for_user(session, writer_id)
            writer_item_ids = {uuid.UUID(it.execution_id) for it in writer_items}
            self.assertIn(ex_widget_id, writer_item_ids)

            # Reader must NOT see it (read access alone never reaches workflows)
            reader_records = await list_persisted_active_executions_for_user(session, reader_id)
            reader_ids = {r.execution_id for r in reader_records}
            self.assertNotIn(ex_widget_id, reader_ids)

            reader_items = await collect_active_executions_for_user(session, reader_id)
            reader_item_ids = {uuid.UUID(it.execution_id) for it in reader_items}
            self.assertNotIn(ex_widget_id, reader_item_ids)

    # =========================================================================
    # 5. Preservation of active execution filters (cancellation, heartbeat staleness, expiry)
    # =========================================================================

    async def test_active_execution_cancellation_and_staleness_filters_preserved(self) -> None:
        """Cancelled or stale active executions remain excluded for team members."""
        now = datetime.now(timezone.utc)
        ex_cancelled_id = uuid.uuid4()
        ex_stale_id = uuid.uuid4()

        async with async_session_maker() as session:
            # Cancelled execution
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_cancelled_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-can",
                    started_at=now,
                    heartbeat_at=now,
                    cancel_requested_at=now,
                )
            )
            # Stale heartbeat execution (> 300s old)
            session.add(
                ActiveWorkflowExecution(
                    execution_id=ex_stale_id,
                    workflow_id=self.wf_id,
                    worker_id="worker-stale",
                    started_at=now - timedelta(seconds=400),
                    heartbeat_at=now - timedelta(seconds=350),
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            records = await list_persisted_active_executions_for_user(session, self.member_id)
            rec_ids = {r.execution_id for r in records}
            self.assertNotIn(ex_cancelled_id, rec_ids)
            self.assertNotIn(ex_stale_id, rec_ids)

    async def test_pending_review_expiry_filter_preserved(self) -> None:
        """Expired pending HITL / Codex reviews remain excluded for team members."""
        now = datetime.now(timezone.utc)
        expired_hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ExecutionHistory(
                    id=expired_hist_id,
                    workflow_id=self.wf_id,
                    started_at=now - timedelta(hours=2),
                    status="pending_review",
                )
            )
            await session.flush()
            session.add(
                HITLRequest(
                    id=uuid.uuid4(),
                    workflow_id=self.wf_id,
                    execution_history_id=expired_hist_id,
                    public_token=f"tok-exp-{uuid.uuid4().hex[:8]}",
                    workflow_name="Shared Team Pipeline",
                    agent_node_id="node-exp",
                    agent_label="Approval",
                    summary="Expired request",
                    status="pending",
                    expires_at=now - timedelta(minutes=10),  # expired
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            reviews = await list_pending_review_executions_for_user(session, self.member_id)
            review_ids = {r.execution_id for r in reviews}
            self.assertNotIn(expired_hist_id, review_ids)


class TeamSharedRecentExecutionsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.owner_id = uuid.uuid4()
        self.member_id = uuid.uuid4()
        self.unrelated_id = uuid.uuid4()
        self.team1_id = uuid.uuid4()
        self.team2_id = uuid.uuid4()
        self.wf_id = uuid.uuid4()

        self.users_to_clean = [self.owner_id, self.member_id, self.unrelated_id]
        self.teams_to_clean = [self.team1_id, self.team2_id]
        self.workflows_to_clean = [self.wf_id]
        self.dashboards_to_clean: list[uuid.UUID] = []

        async with async_session_maker() as session:
            owner = User(
                id=self.owner_id,
                email=f"owner_{self.owner_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Owner",
            )
            member = User(
                id=self.member_id,
                email=f"member_{self.member_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Member",
            )
            unrelated = User(
                id=self.unrelated_id,
                email=f"unrelated_{self.unrelated_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Unrelated",
            )
            session.add_all([owner, member, unrelated])
            await session.flush()

            team1 = Team(id=self.team1_id, name="Team 1", creator_id=self.owner_id)
            team2 = Team(id=self.team2_id, name="Team 2", creator_id=self.owner_id)
            session.add_all([team1, team2])
            await session.flush()

            tm1 = TeamMember(id=uuid.uuid4(), team_id=self.team1_id, user_id=self.member_id)
            tm2 = TeamMember(id=uuid.uuid4(), team_id=self.team2_id, user_id=self.member_id)
            session.add_all([tm1, tm2])
            await session.flush()

            wf = Workflow(
                id=self.wf_id,
                name="Recent Workflow",
                owner_id=self.owner_id,
                nodes=[],
                edges=[],
            )
            session.add(wf)
            await session.flush()

            session.add(
                WorkflowTeamShare(id=uuid.uuid4(), workflow_id=self.wf_id, team_id=self.team1_id)
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        from sqlalchemy import delete

        async with async_session_maker() as session:
            if self.workflows_to_clean:
                await session.execute(
                    delete(ExecutionHistory).where(
                        ExecutionHistory.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(WorkflowTeamShare).where(
                        WorkflowTeamShare.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(WorkflowShare).where(
                        WorkflowShare.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardWidget).where(
                        DashboardWidget.workflow_id.in_(self.workflows_to_clean)
                    )
                )
                await session.execute(
                    delete(Workflow).where(Workflow.id.in_(self.workflows_to_clean))
                )

            if self.dashboards_to_clean:
                await session.execute(
                    delete(DashboardShare).where(
                        DashboardShare.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardTeamShare).where(
                        DashboardTeamShare.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(DashboardWidget).where(
                        DashboardWidget.dashboard_id.in_(self.dashboards_to_clean)
                    )
                )
                await session.execute(
                    delete(Dashboard).where(Dashboard.id.in_(self.dashboards_to_clean))
                )

            if self.teams_to_clean:
                await session.execute(
                    delete(TeamMember).where(TeamMember.team_id.in_(self.teams_to_clean))
                )
                await session.execute(delete(Team).where(Team.id.in_(self.teams_to_clean)))

            if self.users_to_clean:
                await session.execute(delete(User).where(User.id.in_(self.users_to_clean)))
            await session.commit()
        await engine.dispose()

    async def test_team_member_sees_recent_executions(self) -> None:
        """A team member can list recent executions for a workflow shared with their team."""
        now = datetime.now(timezone.utc)
        hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ExecutionHistory(
                    id=hist_id,
                    workflow_id=self.wf_id,
                    started_at=now,
                    status="completed",
                    execution_time_ms=123,
                    trigger_source="manual",
                    outputs={"result": "ok"},
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            # Team member sees it
            member_recent = await get_recent_executions_for_user(session, self.member_id)
            matching_member = [r for r in member_recent if r["workflow_name"] == "Recent Workflow"]
            self.assertEqual(len(matching_member), 1)
            self.assertEqual(matching_member[0]["status"], "completed")
            self.assertEqual(matching_member[0]["execution_time_ms"], 123)

            # Unrelated user does not see it
            unrelated_recent = await get_recent_executions_for_user(session, self.unrelated_id)
            matching_unrelated = [
                r for r in unrelated_recent if r["workflow_name"] == "Recent Workflow"
            ]
            self.assertEqual(len(matching_unrelated), 0)

    async def test_multiple_teams_and_direct_share_do_not_duplicate_recent_executions(self) -> None:
        """A user in multiple teams sharing a workflow receives exactly one entry per execution."""
        now = datetime.now(timezone.utc)
        hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                WorkflowTeamShare(id=uuid.uuid4(), workflow_id=self.wf_id, team_id=self.team2_id)
            )
            session.add(
                WorkflowShare(id=uuid.uuid4(), workflow_id=self.wf_id, user_id=self.member_id)
            )
            session.add(
                ExecutionHistory(
                    id=hist_id,
                    workflow_id=self.wf_id,
                    started_at=now,
                    status="completed",
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            recent = await get_recent_executions_for_user(session, self.member_id)
            matching = [r for r in recent if r["workflow_name"] == "Recent Workflow"]
            self.assertEqual(len(matching), 1)

    async def test_recent_executions_revocation_on_share_removal(self) -> None:
        """Removing WorkflowTeamShare removes workflow executions from get_recent_executions_for_user."""
        from sqlalchemy import delete

        now = datetime.now(timezone.utc)
        hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ExecutionHistory(
                    id=hist_id,
                    workflow_id=self.wf_id,
                    started_at=now,
                    status="completed",
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            recent_before = await get_recent_executions_for_user(session, self.member_id)
            self.assertTrue(any(r["workflow_name"] == "Recent Workflow" for r in recent_before))

            await session.execute(
                delete(WorkflowTeamShare).where(
                    WorkflowTeamShare.workflow_id == self.wf_id,
                    WorkflowTeamShare.team_id == self.team1_id,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            recent_after = await get_recent_executions_for_user(session, self.member_id)
            self.assertFalse(any(r["workflow_name"] == "Recent Workflow" for r in recent_after))

    async def test_recent_executions_revocation_on_team_membership_removal(self) -> None:
        """Removing TeamMember removes workflow executions from get_recent_executions_for_user."""
        from sqlalchemy import delete

        now = datetime.now(timezone.utc)
        hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                ExecutionHistory(
                    id=hist_id,
                    workflow_id=self.wf_id,
                    started_at=now,
                    status="completed",
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            recent_before = await get_recent_executions_for_user(session, self.member_id)
            self.assertTrue(any(r["workflow_name"] == "Recent Workflow" for r in recent_before))

            await session.execute(
                delete(TeamMember).where(
                    TeamMember.team_id == self.team1_id,
                    TeamMember.user_id == self.member_id,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            recent_after = await get_recent_executions_for_user(session, self.member_id)
            self.assertFalse(any(r["workflow_name"] == "Recent Workflow" for r in recent_after))

    async def test_dashboard_widget_recent_executions_write_vs_read_access(self) -> None:
        """Write collaborators on a dashboard can see its widget workflow recent executions; read-only cannot."""
        now = datetime.now(timezone.utc)
        widget_wf_id = uuid.uuid4()
        dashboard_id = uuid.uuid4()
        reader_id = uuid.uuid4()
        writer_id = uuid.uuid4()

        self.workflows_to_clean.append(widget_wf_id)
        self.dashboards_to_clean.append(dashboard_id)
        self.users_to_clean.extend([reader_id, writer_id])

        hist_id = uuid.uuid4()

        async with async_session_maker() as session:
            reader = User(
                id=reader_id,
                email=f"reader_{reader_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Reader",
            )
            writer = User(
                id=writer_id,
                email=f"writer_{writer_id.hex[:8]}@example.com",
                hashed_password="pw",
                name="Writer",
            )
            session.add_all([reader, writer])
            await session.flush()

            dash = Dashboard(
                id=dashboard_id,
                name="Metrics Dashboard",
                owner_id=self.owner_id,
            )
            session.add(dash)
            await session.flush()

            widget_wf = Workflow(
                id=widget_wf_id,
                name="Widget History WF",
                kind="dashboard_widget",
                owner_id=self.owner_id,
                nodes=[],
                edges=[],
            )
            session.add(widget_wf)
            await session.flush()

            widget = DashboardWidget(
                id=uuid.uuid4(),
                dashboard_id=dashboard_id,
                workflow_id=widget_wf_id,
                title="Metrics Widget",
            )
            session.add(widget)

            session.add(
                DashboardShare(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard_id,
                    user_id=reader_id,
                    permission="read",
                )
            )
            session.add(
                DashboardShare(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard_id,
                    user_id=writer_id,
                    permission="write",
                )
            )

            session.add(
                ExecutionHistory(
                    id=hist_id,
                    workflow_id=widget_wf_id,
                    started_at=now,
                    status="completed",
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            # Writer sees it
            writer_recent = await get_recent_executions_for_user(session, writer_id)
            matching_writer = [
                r for r in writer_recent if r["workflow_name"] == "Widget History WF"
            ]
            self.assertEqual(len(matching_writer), 1)

            # Reader does NOT see it
            reader_recent = await get_recent_executions_for_user(session, reader_id)
            matching_reader = [
                r for r in reader_recent if r["workflow_name"] == "Widget History WF"
            ]
            self.assertEqual(len(matching_reader), 0)

    async def test_recent_executions_preserves_filters(self) -> None:
        """get_recent_executions_for_user preserves since_hours and limit filters."""
        now = datetime.now(timezone.utc)
        recent_id = uuid.uuid4()
        old_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add_all(
                [
                    ExecutionHistory(
                        id=recent_id,
                        workflow_id=self.wf_id,
                        started_at=now - timedelta(hours=1),
                        status="completed",
                    ),
                    ExecutionHistory(
                        id=old_id,
                        workflow_id=self.wf_id,
                        started_at=now - timedelta(hours=48),
                        status="completed",
                    ),
                ]
            )
            await session.commit()

        async with async_session_maker() as session:
            # Default since_hours=24 excludes the 48-hour-old execution
            recent_24h = await get_recent_executions_for_user(
                session, self.member_id, since_hours=24
            )
            names_24h = [
                r["workflow_name"] for r in recent_24h if r["workflow_name"] == "Recent Workflow"
            ]
            self.assertEqual(len(names_24h), 1)

            # since_hours=None includes both
            all_recent = await get_recent_executions_for_user(
                session, self.member_id, since_hours=None
            )
            names_all = [
                r["workflow_name"] for r in all_recent if r["workflow_name"] == "Recent Workflow"
            ]
            self.assertEqual(len(names_all), 2)

            # limit=1 caps at 1
            limited = await get_recent_executions_for_user(
                session, self.member_id, since_hours=None, limit=1
            )
            self.assertEqual(len(limited), 1)


if __name__ == "__main__":
    unittest.main()
