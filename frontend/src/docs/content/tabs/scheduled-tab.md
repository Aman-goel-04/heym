# Scheduled Tab

The **Scheduled** tab shows all active cron workflows on a visual calendar. Switch between day, week, and month views to see when your automations are scheduled to run.

<video src="/features/showcase/scheduled.webm" controls playsinline muted preload="metadata" style="width:100%;border-radius:12px;margin:16px 0"></video>
<p class="github-video-link"><a href="../../../../public/features/showcase/scheduled.webm">▶ Watch Scheduled demo</a></p>

## Views

- **Day** – Hour-by-hour timeline for the selected day; each occurrence appears as a block at its scheduled time
- **Week** – Seven-column grid spanning the current week; blocks stack when multiple workflows fire at the same hour
- **Month** – Full month grid; each cell lists the workflows scheduled to run on that day

## Workflow Blocks

Each block displays:

- **Workflow name** – Click to open the workflow canvas directly
- **Cron expression** – Visible on hover, showing the raw schedule (e.g. `0 9 * * 1-5`)

## Navigation

Instance administrators listed in `HEYM_ADMIN_EMAILS` see every owner's schedules when **Show all workflows** is checked. Uncheck it to see only their own workflows. Other users retain the **Show shared with me** option. To retire an employee's automation, use the [instance administration controls](./workflows-tab.md#instance-administration) on the Workflows preview to pause or resume triggers.

- Use the **previous / next** arrows to move across days, weeks, or months
- The **Today** button returns to the current date instantly

## Related

- [Cron Node](../nodes/cron-node.md) – Configure scheduled triggers on the canvas
- [Workflows Tab](./workflows-tab.md) – Manage and organise all workflows
- [Analytics Tab](./analytics-tab.md) – Execution metrics and trends
