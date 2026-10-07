import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRenderer, type App } from "vue";

import { useWorkflowRowStatus, type WorkflowRowStatusApi } from "./useWorkflowRowStatus";
import { workflowApi } from "@/services/api";
import type { ActiveExecutionItem, WorkflowListItem } from "@/types/workflow";

// The source calls onUnmounted(), a COMPONENT lifecycle hook - unlike
// onScopeDispose (what useDialogStackLayer uses), it needs a real component
// instance, not just an active effectScope. Vue silently no-ops the
// registration outside one (a dev warning only), so effectScope() alone
// would leave subscriberCount incrementing forever and pollTimer stuck
// non-null after the first test. This project has no jsdom/@vue/test-utils,
// so build a real component with a no-op custom renderer (createRenderer with
// every host operation a no-op) - no DOM needed, but app.mount()/unmount()
// still run Vue's real component lifecycle including onUnmounted.
const { createApp } = createRenderer<object, object>({
  createElement: () => ({}),
  insert: () => {},
  remove: () => {},
  setElementText: () => {},
  createText: () => ({}),
  createComment: () => ({}),
  setText: () => {},
  patchProp: () => {},
  parentNode: () => null,
  nextSibling: () => null,
});

let trackedApps: App[] = [];

function createSubscriber(): WorkflowRowStatusApi {
  let api!: WorkflowRowStatusApi;
  const app = createApp({
    setup() {
      api = useWorkflowRowStatus();
      return () => null;
    },
  });
  app.mount({});
  trackedApps.push(app);
  return api;
}

function buildWorkflow(overrides: Partial<WorkflowListItem> = {}): WorkflowListItem {
  return {
    id: "wf-1",
    name: "Test workflow",
    description: null,
    folder_id: null,
    first_node_type: null,
    trigger_status: "manual",
    scheduled_for_deletion: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function buildActiveExecution(overrides: Partial<ActiveExecutionItem> = {}): ActiveExecutionItem {
  return {
    execution_id: "exec-1",
    workflow_id: "wf-1",
    workflow_name: "Test workflow",
    started_at: "2026-01-01T00:00:00Z",
    inputs: {},
    running_node_ids: [],
    node_results: [],
    ...overrides,
  };
}

beforeEach(() => {
  // The source calls window.setInterval/clearInterval directly, and this
  // project's vitest config runs composable tests in environment: "node" -
  // no real `window` global. Stub one whose timer methods delegate to the
  // GLOBAL setInterval/clearInterval by name (not a captured reference), so
  // vi.useFakeTimers() - which patches those globals - still intercepts them.
  vi.stubGlobal("window", {
    setInterval: (...args: Parameters<typeof setInterval>) => setInterval(...args),
    clearInterval: (...args: Parameters<typeof clearInterval>) => clearInterval(...args),
  });
  vi.spyOn(workflowApi, "getActiveExecutions").mockResolvedValue([]);
});

afterEach(() => {
  trackedApps.forEach((app) => app.unmount());
  trackedApps = [];
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("statusFor", () => {
  it("scheduled-for-deletion takes priority over running status", async () => {
    vi.mocked(workflowApi.getActiveExecutions).mockResolvedValue([
      buildActiveExecution({ workflow_id: "wf-1" }),
    ]);
    const { statusFor } = createSubscriber();
    const workflow = buildWorkflow({
      id: "wf-1",
      scheduled_for_deletion: "2026-02-01T00:00:00Z",
    });

    await vi.waitFor(() => expect(statusFor(workflow)).toBe("removeScheduled"));
  });

  it("a workflow present in the active-executions response is reported as running", async () => {
    vi.mocked(workflowApi.getActiveExecutions).mockResolvedValue([
      buildActiveExecution({ workflow_id: "wf-1" }),
    ]);
    const { statusFor } = createSubscriber();
    const workflow = buildWorkflow({ id: "wf-1" });

    await vi.waitFor(() => expect(statusFor(workflow)).toBe("running"));
  });

  it("falls back to the workflow's own trigger_status when not running", async () => {
    const { statusFor } = createSubscriber();
    await vi.waitFor(() => expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1));

    const workflow = buildWorkflow({ id: "wf-2", trigger_status: "scheduled" });
    expect(statusFor(workflow)).toBe("scheduled");
  });

  it('defaults to "manual" when trigger_status is missing', async () => {
    const { statusFor } = createSubscriber();
    await vi.waitFor(() => expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1));

    const workflow = {
      ...buildWorkflow({ id: "wf-3" }),
      trigger_status: undefined,
    } as unknown as WorkflowListItem;

    expect(statusFor(workflow)).toBe("manual");
  });
});

describe("polling and refresh", () => {
  it("refreshes running workflow ids immediately on first use", () => {
    createSubscriber();

    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1);
  });

  it("polls again after POLL_INTERVAL_MS elapses", async () => {
    vi.useFakeTimers();
    createSubscriber();
    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(15_000);

    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(2);
  });

  it("a failed refresh keeps the previous running-ids snapshot instead of clearing it", async () => {
    vi.mocked(workflowApi.getActiveExecutions).mockResolvedValueOnce([
      buildActiveExecution({ workflow_id: "wf-1" }),
    ]);
    const { statusFor, refresh } = createSubscriber();
    const workflow = buildWorkflow({ id: "wf-1" });
    await vi.waitFor(() => expect(statusFor(workflow)).toBe("running"));

    vi.mocked(workflowApi.getActiveExecutions).mockRejectedValueOnce(new Error("network error"));
    await refresh();

    expect(statusFor(workflow)).toBe("running");
  });

  it("the returned refresh() function manually triggers a re-fetch", async () => {
    const { statusFor, refresh } = createSubscriber();
    await vi.waitFor(() => expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1));

    vi.mocked(workflowApi.getActiveExecutions).mockResolvedValueOnce([
      buildActiveExecution({ workflow_id: "wf-9" }),
    ]);
    await refresh();

    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(2);
    expect(statusFor(buildWorkflow({ id: "wf-9" }))).toBe("running");
  });

  it("multiple subscribers share a single underlying poll rather than each starting one", async () => {
    vi.useFakeTimers();
    createSubscriber();
    createSubscriber();
    // The second subscriber joins an already-running poll (pollTimer is
    // already set), so startPolling's immediate fetch only fires once total,
    // not once per subscriber.
    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(15_000);

    // One shared interval tick, not one per subscriber.
    expect(workflowApi.getActiveExecutions).toHaveBeenCalledTimes(2);
  });
});

describe("runningCount", () => {
  it("reflects the number of currently running workflow ids", async () => {
    vi.mocked(workflowApi.getActiveExecutions).mockResolvedValue([
      buildActiveExecution({ workflow_id: "wf-1" }),
      buildActiveExecution({ workflow_id: "wf-2", execution_id: "exec-2" }),
    ]);
    const { runningCount } = createSubscriber();

    await vi.waitFor(() => expect(runningCount.value).toBe(2));
  });
});
