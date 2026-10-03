from __future__ import annotations

from app.services.node_execution.base import NodeExecutionContext
from app.services.node_execution.node_activation import (
    find_node_id_by_label,
    persist_node_active_flag,
)


def execute(ctx: NodeExecutionContext) -> object:
    """Execute the enableNode node."""
    self = ctx.executor
    node_data = ctx.node_data

    target_node_label = node_data.get("targetNodeLabel", "")
    if not target_node_label:
        raise ValueError("enableNode requires a targetNodeLabel")

    target_node_id = find_node_id_by_label(self.nodes, target_node_label)
    if not target_node_id:
        raise ValueError(f"Target node with label '{target_node_label}' not found")

    was_inactive = target_node_id in self.inactive_nodes
    self.nodes[target_node_id]["data"]["active"] = True
    self.inactive_nodes.discard(target_node_id)
    # Root nodes (sub-agents, orphan outputs) are skipped on purpose and already scheduled.
    if was_inactive and any(edge.get("target") == target_node_id for edge in self.edges):
        with self.lock:
            self.skipped_nodes.discard(target_node_id)

    persist_node_active_flag(self.workflow_id, target_node_label, True)

    return {"targetNode": target_node_label, "enabled": True}
