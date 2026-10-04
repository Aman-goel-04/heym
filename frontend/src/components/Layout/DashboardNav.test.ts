import { createSSRApp, h, reactive, type Component } from "vue";
import { describe, expect, it, vi } from "vitest";
import { renderToString } from "vue/server-renderer";

import DashboardNav from "@/components/Layout/DashboardNav.vue";

const mockRoute = reactive({
  path: "/",
  query: {} as Record<string, string | string[]>,
});

vi.mock("vue-router", () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}));

async function renderNav(): Promise<string> {
  const app = createSSRApp({
    render: () => h(DashboardNav as Component),
  });
  return renderToString(app);
}

function getActiveTabId(html: string): string | null {
  const match = html.match(/data-tab-id="([^"]+)"[^>]*class="[^"]*bg-primary/);
  return match ? match[1] : null;
}

describe("DashboardNav activeTab", () => {
  it("defaults to workflows when query tab is omitted", async () => {
    mockRoute.path = "/";
    mockRoute.query = {};
    const html = await renderNav();
    expect(getActiveTabId(html)).toBe("workflows");
  });

  it("defaults to workflows for unknown query tab", async () => {
    mockRoute.path = "/";
    mockRoute.query = { tab: "unknown-tab" };
    const html = await renderNav();
    expect(getActiveTabId(html)).toBe("workflows");
  });

  it("resolves special top-level routes regardless of query", async () => {
    mockRoute.path = "/evals";
    mockRoute.query = { tab: "board" };
    expect(getActiveTabId(await renderNav())).toBe("evals");

    mockRoute.path = "/chats";
    mockRoute.query = {};
    expect(getActiveTabId(await renderNav())).toBe("chat");

    mockRoute.path = "/chats/conversation-123";
    mockRoute.query = { tab: "traces" };
    expect(getActiveTabId(await renderNav())).toBe("chat");
  });

  it("resolves datatable sub-paths to datatable", async () => {
    mockRoute.path = "/";
    mockRoute.query = { tab: "datatable/table-xyz" };
    expect(getActiveTabId(await renderNav())).toBe("datatable");
  });

  it("resolves all supported query tabs", async () => {
    const supportedTabs = [
      "workflows",
      "board",
      "schedules",
      "traces",
      "alerts",
      "mcp",
      "analytics",
      "datatable",
      "dashboard",
      "vectorstores",
      "globalvariables",
      "templates",
      "drive",
      "credentials",
      "teams",
      "logs",
    ];

    mockRoute.path = "/";
    for (const tab of supportedTabs) {
      mockRoute.query = { tab };
      const html = await renderNav();
      expect(getActiveTabId(html)).toBe(tab);
    }
  });

  it("resolves array query tab by taking the first element", async () => {
    mockRoute.path = "/";
    mockRoute.query = { tab: ["traces", "board"] };
    const html = await renderNav();
    expect(getActiveTabId(html)).toBe("traces");
  });
});
