import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import type { Alert } from "@/types/alerts";

vi.mock("@/services/api", () => ({
  alertsApi: {
    update: vi.fn(),
  },
}));

import { useToast } from "@/composables/useToast";
import { alertsApi } from "@/services/api";
import { useAlertsStore } from "@/stores/alerts";

const RESUME_DETAIL =
  "You no longer have access to the workflow this alert watches. " +
  "Ask its owner to share it with you again, then resume the alert.";

function makeAlert(enabled: boolean): Alert {
  return { id: "alert-1", name: "Invoice failures", enabled } as Alert;
}

describe("alerts store toggleEnabled", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.mocked(alertsApi.update).mockReset();
    useToast().hideToast();
  });

  it("shows the server's reason when a paused alert cannot be resumed", async () => {
    vi.mocked(alertsApi.update).mockRejectedValue({
      response: { status: 403, data: { detail: RESUME_DETAIL } },
    });
    const store = useAlertsStore();
    const paused = makeAlert(false);
    store.alerts = [paused];

    const result = await store.toggleEnabled(paused);

    const { toastMessage, toastType, toastVisible } = useToast();
    expect(result).toBeNull();
    expect(toastVisible.value).toBe(true);
    expect(toastType.value).toBe("error");
    expect(toastMessage.value).toBe(RESUME_DETAIL);
    expect(store.alerts[0].enabled).toBe(false);
  });

  it("does not show a toast when the toggle succeeds", async () => {
    const resumed = makeAlert(true);
    vi.mocked(alertsApi.update).mockResolvedValue(resumed);
    const store = useAlertsStore();
    store.alerts = [makeAlert(false)];

    const result = await store.toggleEnabled(store.alerts[0]);

    expect(result).toEqual(resumed);
    expect(useToast().toastVisible.value).toBe(false);
    expect(store.alerts[0].enabled).toBe(true);
  });
});
