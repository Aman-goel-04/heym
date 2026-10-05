import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDialogBackHistory } from "./useDialogBackHistory";

/**
 * This project's Vitest config runs in a plain Node environment (no jsdom), so
 * `window`/`document` don't exist by default. This hand-built fake stands in
 * for just enough of the real browser history API to drive push/back/popstate
 * sequencing deterministically: a real history stack, a `pushState` that
 * actually advances it, and a `back()` that pops it and synchronously fires
 * `popstate` to registered listeners, the same way a real browser does.
 */
type FakePopstateEvent = { state: unknown; stopImmediatePropagation: () => void };
type FakePopstateListener = (event: FakePopstateEvent) => void;

function createFakeBrowser() {
  const stack: Array<{ state: unknown; url: string }> = [
    { state: null, url: "https://heym.test/board" },
  ];
  let index = 0;
  const popstateListeners = new Set<FakePopstateListener>();

  function fireCurrentAsPopstate() {
    const event = { state: stack[index].state, stopImmediatePropagation: vi.fn() };
    for (const listener of popstateListeners) listener(event);
    return event;
  }

  const location = {
    get href() {
      return stack[index].url;
    },
  };

  const history = {
    get state() {
      return stack[index].state;
    },
    pushState: vi.fn((state: unknown, _title: string, url: string) => {
      stack.splice(index + 1);
      stack.push({ state, url });
      index += 1;
    }),
    back: vi.fn(() => {
      if (index === 0) return;
      index -= 1;
      fireCurrentAsPopstate();
    }),
  };

  const addEventListener = vi.fn((_type: string, listener: FakePopstateListener) => {
    popstateListeners.add(listener);
  });

  const removeEventListener = vi.fn((_type: string, listener: FakePopstateListener) => {
    popstateListeners.delete(listener);
  });

  return {
    location,
    history,
    addEventListener,
    removeEventListener,
    /** Simulates a real Back/forward gesture arriving independently of this
     * fake's own `history.back()` (e.g. the user's physical back action). */
    dispatchExternalPopstate() {
      if (index === 0) return;
      index -= 1;
      return fireCurrentAsPopstate();
    },
    /** Simulates the URL changing without the dialog's own history-state key
     * being touched (e.g. a router navigation layered on top). */
    navigateSameEntryTo(url: string) {
      stack[index] = { ...stack[index], url };
    },
  };
}

let browser: ReturnType<typeof createFakeBrowser>;

beforeEach(() => {
  browser = createFakeBrowser();
  vi.stubGlobal("window", {
    history: browser.history,
    location: browser.location,
    addEventListener: browser.addEventListener,
    removeEventListener: browser.removeEventListener,
  });
  vi.stubGlobal("document", { body: { dataset: {} as Record<string, string> } });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pushDialogHistoryEntry", () => {
  it("does nothing when the feature is not enabled", () => {
    const { pushDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => false,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    pushDialogHistoryEntry();

    expect(browser.history.pushState).not.toHaveBeenCalled();
  });

  it("pushes exactly one entry, ignoring a second push while it already owns one", () => {
    const { pushDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    pushDialogHistoryEntry();
    pushDialogHistoryEntry();

    expect(browser.history.pushState).toHaveBeenCalledTimes(1);
  });
});

describe("handlePopState (triggered by a real Back navigation)", () => {
  it("calls onBack when its own entry is popped while the dialog is open", () => {
    const onBack = vi.fn();
    const { pushDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack,
    });

    pushDialogHistoryEntry();
    browser.dispatchExternalPopstate();

    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("does not call onBack if the dialog has already closed by the time the pop arrives", () => {
    const onBack = vi.fn();
    let open = true;
    const { pushDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => open,
      onBack,
    });

    pushDialogHistoryEntry();
    open = false;
    browser.dispatchExternalPopstate();

    expect(onBack).not.toHaveBeenCalled();
  });
});

describe("removeDialogHistoryEntry", () => {
  it("does nothing when it never owned a history entry", () => {
    const { removeDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    removeDialogHistoryEntry();

    expect(browser.history.back).not.toHaveBeenCalled();
  });

  it("does nothing when a different history entry has since become current", () => {
    const { pushDialogHistoryEntry, removeDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    pushDialogHistoryEntry();
    // Something else (another dialog, a route change) pushed its own entry on
    // top, so the current entry no longer carries this dialog's state key.
    browser.history.pushState({ other: true }, "", browser.location.href);

    removeDialogHistoryEntry();

    expect(browser.history.back).not.toHaveBeenCalled();
  });

  it("does nothing when the page has navigated to a different URL since the entry was pushed", () => {
    const { pushDialogHistoryEntry, removeDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    pushDialogHistoryEntry();
    // The dialog's own history-state key is still current, but something
    // (e.g. a router navigation) changed the visible URL without pushing a
    // new entry - exactly the "Open live -> editor" case the source comments on.
    browser.navigateSameEntryTo("https://heym.test/workflows/123");

    removeDialogHistoryEntry();

    expect(browser.history.back).not.toHaveBeenCalled();
  });

  it("sets the overlay-ignore flag and calls history.back when nothing else has interfered", () => {
    const { pushDialogHistoryEntry, removeDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack: vi.fn(),
    });

    pushDialogHistoryEntry();
    removeDialogHistoryEntry();

    expect((document.body.dataset as Record<string, string>).heymIgnoreNextOverlayDismiss).toBe(
      "true",
    );
    expect(browser.history.back).toHaveBeenCalledTimes(1);
  });

  it("does not also invoke onBack as a side effect of its own history.back() call", () => {
    // handlePopState's own `ownsHistoryEntry` guard is what actually prevents
    // double-handling here: removeDialogHistoryEntry flips that flag to false
    // before it ever calls history.back(), so even though back() synchronously
    // fires popstate on this fake, handlePopState sees ownsHistoryEntry already
    // false and bails out. This test does not prove listener-removal ordering;
    // see the dedicated listener-cleanup test below for that.
    const onBack = vi.fn();
    const { pushDialogHistoryEntry, removeDialogHistoryEntry } = useDialogBackHistory({
      enabled: () => true,
      isOpen: () => true,
      onBack,
    });

    pushDialogHistoryEntry();
    removeDialogHistoryEntry();

    expect(onBack).not.toHaveBeenCalled();
  });

  it("always removes the popstate listener, even on every early-return path", () => {
    // Direct check on cleanup itself, since the previous test only proves
    // onBack doesn't double-fire, not that the listener was actually removed.
    // A leaked listener here would mean a *later*, unrelated popstate (e.g. a
    // different dialog's own back-dismissal) wrongly re-triggers this one.
    const scenarios: Array<[string, (push: () => void) => void]> = [
      ["never owned an entry", () => {
        // deliberately never call push
      }],
      ["a different entry became current", (push) => {
        push();
        browser.history.pushState({ other: true }, "", browser.location.href);
      }],
      ["navigated to a different URL", (push) => {
        push();
        browser.navigateSameEntryTo("https://heym.test/workflows/123");
      }],
      ["happy path", (push) => {
        push();
      }],
    ];

    for (const [, setUp] of scenarios) {
      browser = createFakeBrowser();
      vi.stubGlobal("window", {
        history: browser.history,
        location: browser.location,
        addEventListener: browser.addEventListener,
        removeEventListener: browser.removeEventListener,
      });

      const { pushDialogHistoryEntry, removeDialogHistoryEntry } = useDialogBackHistory({
        enabled: () => true,
        isOpen: () => true,
        onBack: vi.fn(),
      });

      setUp(pushDialogHistoryEntry);
      browser.removeEventListener.mockClear();

      removeDialogHistoryEntry();

      expect(browser.removeEventListener).toHaveBeenCalledWith(
        "popstate",
        expect.any(Function),
        expect.anything(),
      );
    }
  });
});
