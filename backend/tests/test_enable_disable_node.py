"""enableNode and disableNode: in-run state, persisted flag, registry and prompt coverage."""

import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.api.ai_assistant import _build_workflow_content_summary
from app.db.models import Workflow
from app.services.cluster.node_placement import NODE_PLACEMENT, Placement
from app.services.node_execution import registry
from app.services.node_execution.base import NodeExecutionContext
from app.services.node_execution.nodes import disable_node_node, enable_node_node
from app.services.workflow_dsl_prompt import WORKFLOW_DSL_SYSTEM_PROMPT
from app.services.workflow_executor import WorkflowExecutor, execute_workflow


def _node(node_id: str, node_type: str, **data: object) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_id, **data},
    }


def _edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}", "source": source, "target": target}


def _context(executor: WorkflowExecutor, node: dict) -> NodeExecutionContext:
    return NodeExecutionContext(
        executor=executor,
        node_id=node["id"],
        inputs={},
        allow_branch_skip=False,
        start_time=0.0,
        node=node,
        node_type=node["type"],
        node_data=node["data"],
        node_label=node["data"]["label"],
    )


def _session_factory(workflow: Workflow | None) -> tuple[MagicMock, MagicMock]:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow
    session = MagicMock()
    session.__enter__.return_value = db
    return MagicMock(return_value=session), db


def _stored_workflow(nodes: list[dict]) -> Workflow:
    return Workflow(id=uuid.uuid4(), name="wf", nodes=nodes, edges=[])


class EnableNodeHandlerTests(unittest.TestCase):
    def test_reactivates_a_disabled_node_that_has_not_run_yet(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="later")
        later = _node("later", "set", active=False)
        executor = WorkflowExecutor(nodes=[enable, later], edges=[_edge("enable", "later")])
        self.assertIn("later", executor.skipped_nodes)
        self.assertIn("later", executor.inactive_nodes)

        output = enable_node_node.execute(_context(executor, enable))

        self.assertEqual(output, {"targetNode": "later", "enabled": True})
        self.assertIs(executor.nodes["later"]["data"]["active"], True)
        self.assertNotIn("later", executor.inactive_nodes)
        self.assertNotIn("later", executor.skipped_nodes)

    def test_a_node_without_inputs_stays_skipped(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="orphan")
        orphan = _node("orphan", "output", active=False, message="hi")
        executor = WorkflowExecutor(nodes=[enable, orphan], edges=[])
        self.assertIn("orphan", executor.skipped_nodes)

        enable_node_node.execute(_context(executor, enable))

        self.assertIs(executor.nodes["orphan"]["data"]["active"], True)
        self.assertNotIn("orphan", executor.inactive_nodes)
        self.assertIn("orphan", executor.skipped_nodes)

    def test_an_active_node_skipped_for_another_reason_stays_skipped(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="later")
        later = _node("later", "set")
        executor = WorkflowExecutor(nodes=[enable, later], edges=[_edge("enable", "later")])
        executor.skipped_nodes.add("later")

        enable_node_node.execute(_context(executor, enable))

        self.assertIn("later", executor.skipped_nodes)

    def test_requires_a_target_label(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="")
        executor = WorkflowExecutor(nodes=[enable], edges=[])

        with self.assertRaisesRegex(ValueError, "requires a targetNodeLabel"):
            enable_node_node.execute(_context(executor, enable))

    def test_rejects_an_unknown_target_label(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="missing")
        executor = WorkflowExecutor(nodes=[enable], edges=[])

        with self.assertRaisesRegex(ValueError, "'missing' not found"):
            enable_node_node.execute(_context(executor, enable))

    def test_skips_the_database_without_a_workflow_id(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="later")
        later = _node("later", "set", active=False)
        executor = WorkflowExecutor(nodes=[enable, later], edges=[_edge("enable", "later")])
        factory, _ = _session_factory(None)

        with patch("app.db.session.SessionLocal", factory):
            enable_node_node.execute(_context(executor, enable))

        factory.assert_not_called()

    def test_persists_active_true_on_the_saved_workflow(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="later")
        later = _node("later", "set", active=False)
        stored = _stored_workflow(
            [_node("enable", "enableNode"), _node("later", "set", active=False)]
        )
        executor = WorkflowExecutor(
            nodes=[enable, later], edges=[_edge("enable", "later")], workflow_id=stored.id
        )
        factory, db = _session_factory(stored)

        with patch("app.db.session.SessionLocal", factory):
            enable_node_node.execute(_context(executor, enable))

        by_label = {node["data"]["label"]: node for node in stored.nodes}
        self.assertIs(by_label["later"]["data"]["active"], True)
        self.assertNotIn("active", by_label["enable"]["data"])
        db.commit.assert_called_once()

    def test_a_missing_saved_workflow_is_not_an_error(self) -> None:
        enable = _node("enable", "enableNode", targetNodeLabel="later")
        later = _node("later", "set", active=False)
        executor = WorkflowExecutor(
            nodes=[enable, later], edges=[_edge("enable", "later")], workflow_id=uuid.uuid4()
        )
        factory, db = _session_factory(None)

        with patch("app.db.session.SessionLocal", factory):
            output = enable_node_node.execute(_context(executor, enable))

        self.assertTrue(output["enabled"])
        db.commit.assert_not_called()


class DisableNodeHandlerTests(unittest.TestCase):
    def test_marks_the_target_inactive_and_skipped(self) -> None:
        disable = _node("disable", "disableNode", targetNodeLabel="later")
        later = _node("later", "set")
        executor = WorkflowExecutor(nodes=[disable, later], edges=[_edge("disable", "later")])

        output = disable_node_node.execute(_context(executor, disable))

        self.assertEqual(output, {"targetNode": "later", "disabled": True})
        self.assertIs(executor.nodes["later"]["data"]["active"], False)
        self.assertIn("later", executor.inactive_nodes)
        self.assertIn("later", executor.skipped_nodes)

    def test_persists_active_false_on_the_saved_workflow(self) -> None:
        disable = _node("disable", "disableNode", targetNodeLabel="later")
        later = _node("later", "set")
        stored = _stored_workflow([_node("disable", "disableNode"), _node("later", "set")])
        executor = WorkflowExecutor(
            nodes=[disable, later], edges=[_edge("disable", "later")], workflow_id=stored.id
        )
        factory, db = _session_factory(stored)

        with patch("app.db.session.SessionLocal", factory):
            disable_node_node.execute(_context(executor, disable))

        by_label = {node["data"]["label"]: node for node in stored.nodes}
        self.assertIs(by_label["later"]["data"]["active"], False)
        db.commit.assert_called_once()

    def test_rejects_an_unknown_target_label(self) -> None:
        disable = _node("disable", "disableNode", targetNodeLabel="missing")
        executor = WorkflowExecutor(nodes=[disable], edges=[])

        with self.assertRaisesRegex(ValueError, "'missing' not found"):
            disable_node_node.execute(_context(executor, disable))


class EnableNodeWorkflowTests(unittest.TestCase):
    def _run(self, nodes: list[dict], edges: list[dict]) -> dict[str, str]:
        factory, _ = _session_factory(None)
        with patch("app.db.session.SessionLocal", factory):
            result = execute_workflow(
                workflow_id=uuid.uuid4(),
                nodes=nodes,
                edges=edges,
                inputs={"headers": {}, "query": {}, "body": {"text": "hi"}},
            )
        self.assertEqual(result.status, "success")
        return {row["node_label"]: row["status"] for row in result.node_results}

    def test_an_enabled_node_runs_in_the_same_execution(self) -> None:
        nodes = [
            _node("in", "textInput"),
            _node("enableLater", "enableNode", targetNodeLabel="later"),
            _node("later", "set", active=False, mappings=[{"key": "ran", "value": "yes"}]),
            _node("out", "output", message="$later.ran"),
        ]
        edges = [_edge("in", "enableLater"), _edge("enableLater", "later"), _edge("later", "out")]

        statuses = self._run(nodes, edges)

        self.assertEqual(statuses["enableLater"], "success")
        self.assertEqual(statuses["later"], "success")

    def test_a_disabled_node_stays_skipped_without_an_enable_node(self) -> None:
        nodes = [
            _node("in", "textInput"),
            _node("later", "set", active=False, mappings=[{"key": "ran", "value": "yes"}]),
            _node("out", "output", message="done"),
        ]
        edges = [_edge("in", "later"), _edge("later", "out")]

        statuses = self._run(nodes, edges)

        self.assertEqual(statuses["later"], "skipped")


class EnableNodeRegistrationTests(unittest.TestCase):
    def test_the_node_has_a_handler_and_runs_anywhere(self) -> None:
        self.assertIs(registry.get_node_handler("enableNode"), enable_node_node.execute)
        self.assertEqual(NODE_PLACEMENT["enableNode"], Placement.ANYWHERE)

    def test_the_dsl_prompt_documents_enable_node_and_the_wait_limit(self) -> None:
        self.assertIn("### 20b. enableNode (Enable Another Node)", WORKFLOW_DSL_SYSTEM_PROMPT)
        self.assertIn('"type": "enableNode"', WORKFLOW_DSL_SYSTEM_PROMPT)
        self.assertIn("maximum is 15 minutes (900000 ms)", WORKFLOW_DSL_SYSTEM_PROMPT)


class WaitNodeSummaryTests(unittest.TestCase):
    def test_the_assistant_summary_reports_the_wait_duration_in_milliseconds(self) -> None:
        workflow = _stored_workflow([_node("pause", "wait", duration=900000)])

        summary = _build_workflow_content_summary(workflow)

        self.assertEqual(summary["nodes"][0]["duration_ms"], 900000)
        self.assertNotIn("duration_seconds", summary["nodes"][0])


if __name__ == "__main__":
    unittest.main()
