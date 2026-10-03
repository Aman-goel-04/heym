from __future__ import annotations

from app.services.node_execution.base import NodeExecutionContext
from app.services.node_execution.node_activation import (
    find_node_id_by_label,
    persist_node_active_flag,
)


def execute(ctx: NodeExecutionContext) -> object:
    """Execute the disableNode node."""
    self = ctx.executor
    node_data = ctx.node_data

    target_node_label = node_data.get("targetNodeLabel", "")
    if not target_node_label:
        raise ValueError("disableNode requires a targetNodeLabel")

    target_node_id = find_node_id_by_label(self.nodes, target_node_label)
    if not target_node_id:
        raise ValueError(f"Target node with label '{target_node_label}' not found")

    self.nodes[target_node_id]["data"]["active"] = False
    self.inactive_nodes.add(target_node_id)
    with self.lock:
        self.skipped_nodes.add(target_node_id)

    persist_node_active_flag(self.workflow_id, target_node_label, False)

    output = {
        "targetNode": target_node_label,
        "disabled": True,
    }
    return output
