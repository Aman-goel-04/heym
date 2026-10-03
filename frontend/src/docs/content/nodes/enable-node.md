# Enable Node

The **Enable Node** node turns another node back on. When executed, it sets the target node's `active` to `true` and persists the change. It is the counterpart of [Disable Node](./disable-node.md): use it to re-arm a [Cron](./cron-node.md) trigger or any node that was switched off.

## Overview

| Property | Value |
|----------|-------|
| Inputs | 1 |
| Outputs | 1 |
| Output | `$nodeLabel.targetNode`, `$nodeLabel.enabled` |

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `label` | string | Node identifier (camelCase) |
| `targetNodeLabel` | string | Label of the node to enable (e.g. `hourlyCheck`) |

## Behavior

- The target is saved as active, so future runs and triggers see it. A re-enabled Cron trigger fires again at its next scheduled time.
- If the target sits downstream of the Enable Node and has not run yet, it also runs in the current execution.
- Enabling a node that is already active changes nothing; the output still reports `enabled: true`.
- The node fails with an error when `targetNodeLabel` is empty or no node has that label.

## Use Case

Start a [Cron](./cron-node.md) trigger on demand. Save the Cron node switched off, and give the workflow a second entry point such as an [Input](./input-node.md) trigger. A request to that entry point runs the Enable Node, and the Cron starts firing.

A switched-off trigger never starts a run by itself, so the Enable Node must sit behind an entry point that can run.

## Example

```json
{
  "type": "enableNode",
  "data": {
    "label": "startCron",
    "targetNodeLabel": "hourlyCheck"
  }
}
```

Flow: Input → Enable Node (target: cron) → Output. The Cron node `hourlyCheck` is saved as disabled in the same workflow and runs its own HTTP check branch once enabled.

## Related

- [Node Types](../reference/node-types.md) – Overview of all node types
- [Disable Node](./disable-node.md) – Switch a node off from inside a workflow
- [Cron Node](./cron-node.md) – Trigger to enable
- [Condition Node](./condition-node.md) – Branch before enabling
- [Canvas Features](../reference/canvas-features.md) – Enable or disable a node by hand
