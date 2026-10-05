"""Unit tests for agent persistent memory helpers."""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import delete, select, text
from sqlalchemy.dialects import postgresql

from app.db.models import AgentMemoryEdge, AgentMemoryNode, User, Workflow
from app.db.session import SessionLocal
from app.services import agent_memory_service as agent_memory_service_mod
from app.services.agent_memory_service import (
    _is_unsupported_json_object_response_format,
    _trace_context_for_memory_job,
    apply_parsed_extraction_sync,
    augment_system_instruction_with_memory,
    entity_name_equals_ci,
    format_conversation_for_memory,
    format_memory_graph_for_prompt,
    memory_extraction_targets_for_agent_node,
    merge_memory_share_targets,
    normalize_relationship_type,
    parse_llm_json_block,
    prune_isolated_nodes_sync,
    remove_conflicting_outgoing_edges_sync,
)
from app.services.llm_trace import LLMTraceContext


def _is_db_reachable() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


class UnsupportedJsonObjectResponseFormatTests(unittest.TestCase):
    def test_detects_lm_studio_style_error(self) -> None:
        msg = "Error code: 400 - {'error': \"'response_format.type' must be 'json_schema' or 'text'\"}"
        self.assertTrue(_is_unsupported_json_object_response_format(ValueError(msg)))

    def test_other_errors_false(self) -> None:
        self.assertFalse(_is_unsupported_json_object_response_format(ValueError("rate limit")))


class EntityNameEqualsCiTests(unittest.TestCase):
    def test_sql_lowers_both_column_and_literal(self) -> None:
        """PostgreSQL vs Python lower() must not diverge (e.g. Turkish İ)."""
        wf = uuid.uuid4()
        stmt = select(AgentMemoryNode).where(
            AgentMemoryNode.workflow_id == wf,
            entity_name_equals_ci(AgentMemoryNode.entity_name, "İstanbul"),
        )
        sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertGreaterEqual(sql.count("lower"), 2)


class NormalizeRelationshipTypeTests(unittest.TestCase):
    def test_collapses_whitespace_and_case(self) -> None:
        self.assertEqual(normalize_relationship_type("  Works   FOR "), "works for")

    def test_underscores_to_spaces(self) -> None:
        self.assertEqual(normalize_relationship_type("works_for"), "works for")

    def test_lives_in_normalizes_for_single_slot(self) -> None:
        norm = normalize_relationship_type("lives_in")
        self.assertEqual(norm, "lives in")
        self.assertIn(norm, agent_memory_service_mod._SINGLE_SLOT_OUTGOING_REL_TYPES)


class MemoryExtractionPromptTests(unittest.TestCase):
    def test_system_prompt_documents_revoked_entities(self) -> None:
        self.assertIn("revoked_entities", agent_memory_service_mod._MEMORY_SYSTEM)


class ParseLlmJsonBlockTests(unittest.TestCase):
    def test_plain_object(self) -> None:
        out = parse_llm_json_block('{"entities": [], "relationships": []}')
        self.assertEqual(out["entities"], [])
        self.assertEqual(out["relationships"], [])

    def test_strips_markdown_fence(self) -> None:
        raw = """```json
{"entities": [{"name": "Ada"}], "relationships": []}
```"""
        out = parse_llm_json_block(raw)
        self.assertEqual(len(out["entities"]), 1)
        self.assertEqual(out["entities"][0]["name"], "Ada")


class FormatConversationForMemoryTests(unittest.TestCase):
    def test_includes_final_text_and_tools(self) -> None:
        blob = format_conversation_for_memory(
            "Hello",
            {
                "text": "Done",
                "tool_calls": [
                    {
                        "name": "call_sub_agent",
                        "arguments": {"sub_agent_label": "a", "prompt": "p"},
                        "result": {"text": "sub out"},
                    }
                ],
            },
        )
        self.assertIn("Hello", blob)
        self.assertIn("Done", blob)
        self.assertIn("call_sub_agent", blob)
        self.assertIn("sub out", blob)

    def test_skips_compression_marker(self) -> None:
        blob = format_conversation_for_memory(
            "x",
            {
                "text": "y",
                "tool_calls": [{"name": "_context_compression", "arguments": {}, "result": {}}],
            },
        )
        self.assertNotIn("_context_compression", blob)


class FormatMemoryGraphForPromptTests(unittest.TestCase):
    def test_empty_nodes_returns_none(self) -> None:
        self.assertIsNone(format_memory_graph_for_prompt([], []))

    def test_entities_and_edges(self) -> None:
        aid = uuid.uuid4()
        bid = uuid.uuid4()
        a = SimpleNamespace(
            id=aid,
            entity_name="Acme Corp",
            entity_type="organization",
            properties={},
        )
        b = SimpleNamespace(
            id=bid,
            entity_name="dark roast",
            entity_type="preference",
            properties={"strength": "high"},
        )
        e = SimpleNamespace(
            source_node_id=aid,
            target_node_id=bid,
            relationship_type="prefers",
        )
        text = format_memory_graph_for_prompt([a, b], [e])
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("Acme Corp", text)
        self.assertIn("prefers", text)
        self.assertIn("dark roast", text)


class TraceContextForMemoryJobTests(unittest.TestCase):
    def test_sets_source_agent_memory(self) -> None:
        uid = uuid.uuid4()
        cid = uuid.uuid4()
        wf = uuid.uuid4()
        base = LLMTraceContext(
            user_id=uid,
            credential_id=cid,
            workflow_id=wf,
            node_id="n1",
            node_label="Agent",
            source="workflow",
        )
        mem = _trace_context_for_memory_job(base)
        self.assertIsNotNone(mem)
        assert mem is not None
        self.assertEqual(mem.source, "agent_memory")
        self.assertEqual(mem.user_id, uid)
        self.assertEqual(mem.credential_id, cid)
        self.assertEqual(mem.workflow_id, wf)
        self.assertEqual(mem.node_id, "n1")

    def test_none_stays_none(self) -> None:
        self.assertIsNone(_trace_context_for_memory_job(None))


class MergeMemoryShareTargetsTests(unittest.TestCase):
    def test_collects_owner_for_peer(self) -> None:
        wf = str(uuid.uuid4())
        nodes = {
            "owner-a": {
                "type": "agent",
                "data": {
                    "label": "Alpha",
                    "memoryShares": [{"peerCanvasNodeId": "peer-b", "permission": "read"}],
                },
            },
        }
        got = merge_memory_share_targets(nodes, wf, wf, "peer-b")
        self.assertEqual(got, [("owner-a", "Alpha", "read")])

    def test_write_wins_over_read(self) -> None:
        wf = str(uuid.uuid4())
        nodes = {
            "owner-a": {
                "type": "agent",
                "data": {
                    "label": "Alpha",
                    "memoryShares": [
                        {"peerCanvasNodeId": "peer-b", "permission": "read"},
                        {"peerCanvasNodeId": "peer-b", "permission": "write"},
                    ],
                },
            },
        }
        got = merge_memory_share_targets(nodes, wf, wf, "peer-b")
        self.assertEqual(got, [("owner-a", "Alpha", "write")])

    def test_cross_workflow_peer_matches_peer_workflow_id(self) -> None:
        wf_owner = "wf1"
        wf_peer = "wf2"
        nodes = {
            "owner-a": {
                "type": "agent",
                "data": {
                    "label": "Alpha",
                    "memoryShares": [
                        {
                            "peerWorkflowId": wf_peer,
                            "peerCanvasNodeId": "peer-b",
                            "permission": "read",
                        }
                    ],
                },
            },
        }
        got = merge_memory_share_targets(nodes, wf_owner, wf_peer, "peer-b")
        self.assertEqual(got, [("owner-a", "Alpha", "read")])


class MemoryExtractionTargetsTests(unittest.TestCase):
    def test_own_and_write_share(self) -> None:
        wf = uuid.uuid4()
        nodes = {
            "mem-owner": {
                "type": "agent",
                "data": {
                    "memoryShares": [{"peerCanvasNodeId": "runner", "permission": "write"}],
                },
            },
        }
        got = memory_extraction_targets_for_agent_node(nodes, wf, "runner", True, None)
        self.assertEqual(
            {(str(x[0]), x[1]) for x in got}, {(str(wf), "runner"), (str(wf), "mem-owner")}
        )

    def test_read_share_no_extra_target(self) -> None:
        wf = uuid.uuid4()
        nodes = {
            "mem-owner": {
                "type": "agent",
                "data": {
                    "memoryShares": [{"peerCanvasNodeId": "runner", "permission": "read"}],
                },
            },
        }
        got = memory_extraction_targets_for_agent_node(nodes, wf, "runner", False, None)
        self.assertEqual(got, [])


class AugmentSystemInstructionTests(unittest.TestCase):
    def test_disabled_passthrough(self) -> None:
        out = augment_system_instruction_with_memory(
            "base",
            uuid.uuid4(),
            "n1",
            enabled=False,
        )
        self.assertEqual(out, "base")

    def test_missing_workflow_or_node_passthrough(self) -> None:
        self.assertEqual(
            augment_system_instruction_with_memory("base", None, "n1", enabled=True),
            "base",
        )
        self.assertEqual(
            augment_system_instruction_with_memory("base", uuid.uuid4(), None, enabled=True),
            "base",
        )

    def test_appends_block_when_graph_present(self) -> None:
        wf = uuid.uuid4()
        block = "**Entities:**\n- Ada (person)"
        with patch(
            "app.services.agent_memory_service.load_agent_memory_prompt_block_sync",
            return_value=block,
        ):
            out = augment_system_instruction_with_memory(
                "You are helpful.",
                wf,
                "node-a",
                enabled=True,
            )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("You are helpful.", out)
        self.assertIn("Persistent memory", out)
        self.assertIn("Ada", out)

    def test_graph_only_when_no_system_text(self) -> None:
        wf = uuid.uuid4()
        with patch(
            "app.services.agent_memory_service.load_agent_memory_prompt_block_sync",
            return_value="- X (topic)",
        ):
            out = augment_system_instruction_with_memory(None, wf, "n", enabled=True)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertTrue(out.startswith("### Persistent memory"))

    def test_shared_memory_when_own_disabled(self) -> None:
        wf = uuid.uuid4()
        nodes = {
            "owner-a": {
                "type": "agent",
                "data": {
                    "label": "Alpha",
                    "memoryShares": [{"peerCanvasNodeId": "peer-b", "permission": "read"}],
                },
            },
        }

        def fake_load(_wid: uuid.UUID, cid: str) -> str | None:
            if cid == "owner-a":
                return "- Shared (topic)"
            return None

        with patch(
            "app.services.agent_memory_service.load_agent_memory_prompt_block_sync",
            side_effect=fake_load,
        ):
            out = augment_system_instruction_with_memory(
                "base",
                wf,
                "peer-b",
                enabled=False,
                workflow_nodes=nodes,
                trace_user_id=None,
            )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("base", out)
        self.assertIn("Shared agent memory", out)
        self.assertIn("Shared (topic)", out)


class MemoryPruneScopingPostgreSqlTests(unittest.TestCase):
    """Issue #658: isolated-node pruning must be scoped to candidate node ids,
    never to every zero-edge node under the canvas node.
    """

    def setUp(self) -> None:
        if not _is_db_reachable():
            self.skipTest("PostgreSQL database is not reachable")

        self.user_id = uuid.uuid4()
        self.workflow_id = uuid.uuid4()
        self.canvas_node_id = "agent-1"

        with SessionLocal() as db:
            db.add(
                User(
                    id=self.user_id,
                    email=f"prune-test-{self.user_id}@example.com",
                    hashed_password="hashed_pw",
                    name="Prune Tester",
                )
            )
            db.flush()
            db.add(
                Workflow(
                    id=self.workflow_id,
                    name="Prune test workflow",
                    owner_id=self.user_id,
                )
            )
            db.commit()

    def tearDown(self) -> None:
        if not _is_db_reachable():
            return
        with SessionLocal() as db:
            db.execute(delete(Workflow).where(Workflow.id == self.workflow_id))
            db.commit()

    def _add_node(self, db, name: str, entity_type: str = "person") -> AgentMemoryNode:
        node = AgentMemoryNode(
            id=uuid.uuid4(),
            workflow_id=self.workflow_id,
            canvas_node_id=self.canvas_node_id,
            entity_name=name,
            entity_type=entity_type,
            properties={},
            confidence=1.0,
        )
        db.add(node)
        db.flush()
        return node

    def _add_edge(
        self, db, source: AgentMemoryNode, target: AgentMemoryNode, relationship_type: str
    ) -> AgentMemoryEdge:
        edge = AgentMemoryEdge(
            id=uuid.uuid4(),
            workflow_id=self.workflow_id,
            canvas_node_id=self.canvas_node_id,
            source_node_id=source.id,
            target_node_id=target.id,
            relationship_type=relationship_type,
            properties={},
            confidence=1.0,
        )
        db.add(edge)
        db.flush()
        return edge

    def test_isolated_node_outside_candidate_scope_survives(self) -> None:
        # Carol has no edges at all, but editing an unrelated Alice/Bob edge must
        # never sweep her - she is outside the candidate scope entirely. This is
        # the exact regression from #658 (editing one edge silently deleted an
        # unrelated isolated node).
        with SessionLocal() as db:
            alice = self._add_node(db, "Alice")
            bob = self._add_node(db, "Bob")
            carol = self._add_node(db, "Carol")
            self._add_edge(db, alice, bob, "knows")
            db.commit()

            prune_isolated_nodes_sync(
                db,
                self.workflow_id,
                self.canvas_node_id,
                candidate_node_ids=frozenset({alice.id, bob.id}),
            )
            db.commit()

            remaining = {
                n.id
                for n in db.execute(
                    select(AgentMemoryNode).where(AgentMemoryNode.workflow_id == self.workflow_id)
                )
                .scalars()
                .all()
            }
        self.assertIn(alice.id, remaining)
        self.assertIn(bob.id, remaining)
        self.assertIn(carol.id, remaining)

    def test_isolated_node_inside_candidate_scope_is_removed(self) -> None:
        with SessionLocal() as db:
            dave = self._add_node(db, "Dave")
            db.commit()

            prune_isolated_nodes_sync(
                db,
                self.workflow_id,
                self.canvas_node_id,
                candidate_node_ids=frozenset({dave.id}),
            )
            db.commit()

            remaining = {
                n.id
                for n in db.execute(
                    select(AgentMemoryNode).where(AgentMemoryNode.workflow_id == self.workflow_id)
                )
                .scalars()
                .all()
            }
        self.assertNotIn(dave.id, remaining)

    def test_deleting_the_only_edge_removes_both_now_isolated_endpoints(self) -> None:
        # Matches the delete_memory_edge endpoint's behavior: deleting an edge
        # scopes the follow-up prune to exactly that edge's two endpoints, so if
        # either one is left with no other edges it is removed too.
        with SessionLocal() as db:
            alice = self._add_node(db, "Alice")
            bob = self._add_node(db, "Bob")
            edge = self._add_edge(db, alice, bob, "knows")
            db.commit()

            endpoint_ids = frozenset({edge.source_node_id, edge.target_node_id})
            db.execute(delete(AgentMemoryEdge).where(AgentMemoryEdge.id == edge.id))
            db.flush()
            prune_isolated_nodes_sync(
                db, self.workflow_id, self.canvas_node_id, candidate_node_ids=endpoint_ids
            )
            db.commit()

            remaining = {
                n.id
                for n in db.execute(
                    select(AgentMemoryNode).where(AgentMemoryNode.workflow_id == self.workflow_id)
                )
                .scalars()
                .all()
            }
        self.assertNotIn(alice.id, remaining)
        self.assertNotIn(bob.id, remaining)

    def test_remove_conflicting_outgoing_edges_returns_only_affected_node_ids(self) -> None:
        with SessionLocal() as db:
            alice = self._add_node(db, "Alice")
            old_co = self._add_node(db, "OldCo", entity_type="organization")
            new_co = self._add_node(db, "NewCo", entity_type="organization")
            self._add_edge(db, alice, old_co, "works for")
            db.commit()

            affected = remove_conflicting_outgoing_edges_sync(
                db,
                self.workflow_id,
                self.canvas_node_id,
                alice.id,
                "works for",
                new_co.id,
            )
            db.commit()

        self.assertEqual(affected, {alice.id, old_co.id})
        self.assertNotIn(new_co.id, affected)

    def test_extraction_merge_prunes_only_the_replaced_employer_not_a_manual_node(self) -> None:
        # mbakgun's point 3 + the "works for" replacement case: a new "works for"
        # edge from the LLM extraction replaces the old employer link, which
        # should sweep the now-isolated old employer - but a manually added
        # standalone node (Carol) must survive the same merge untouched.
        with SessionLocal() as db:
            alice = self._add_node(db, "Alice")
            old_co = self._add_node(db, "OldCo", entity_type="organization")
            self._add_edge(db, alice, old_co, "works for")
            self._add_node(db, "Carol")
            db.commit()

            apply_parsed_extraction_sync(
                db,
                self.workflow_id,
                self.canvas_node_id,
                {
                    "entities": [
                        {"name": "Alice", "type": "person", "confidence": 1.0},
                        {"name": "NewCo", "type": "organization", "confidence": 1.0},
                    ],
                    "relationships": [
                        {
                            "source": "Alice",
                            "target": "NewCo",
                            "type": "works for",
                            "confidence": 1.0,
                        }
                    ],
                },
            )
            db.commit()

            remaining_nodes = {
                n.entity_name: n
                for n in db.execute(
                    select(AgentMemoryNode).where(AgentMemoryNode.workflow_id == self.workflow_id)
                )
                .scalars()
                .all()
            }

        self.assertNotIn("OldCo", remaining_nodes)
        self.assertIn("Alice", remaining_nodes)
        self.assertIn("NewCo", remaining_nodes)
        self.assertIn("Carol", remaining_nodes)


if __name__ == "__main__":
    unittest.main()
