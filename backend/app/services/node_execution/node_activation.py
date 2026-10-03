"""Shared lookup and persistence for the nodes that switch another node on or off."""

from __future__ import annotations

import uuid


def find_node_id_by_label(nodes: dict[str, dict], label: str) -> str | None:
    """Return the id of the first node whose ``data.label`` matches, or None."""
    for node_id, node in nodes.items():
        if node.get("data", {}).get("label") == label:
            return node_id
    return None


def persist_node_active_flag(workflow_id: uuid.UUID | None, node_label: str, active: bool) -> None:
    """Store the active flag on the saved workflow so later runs and triggers see it."""
    if not workflow_id:
        return

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import Workflow
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
        if workflow:
            updated_nodes = []
            for wf_node in workflow.nodes:
                if wf_node.get("data", {}).get("label") == node_label:
                    wf_node["data"]["active"] = active
                updated_nodes.append(wf_node)
            workflow.nodes = updated_nodes
            flag_modified(workflow, "nodes")
            db.commit()
