import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRenderer, type App } from "vue";

import { addToDialogStack, removeFromDialogStack } from "./useDialogStack";
import {
  DISMISS_OVERLAYS_EVENT,
  dismissAllOverlays,
  onDismissOverlays,
  pushOverlayState,
  useOverlayBackHandler,
} from "./useOverlayBackHandler";

// This file runs in environment: "node" (see vitest.config.ts). Node's global
// scope has a real EventTarget/Event/CustomEvent (used as-is below), but NOT
// window, document, history, HTMLElement, or KeyboardEvent - all faked here.
//
// `window` needs real add/remove/dispatch behavior (not vi.fn() stubs), since
// the source registers real listeners on it and this file needs those
// listeners to actually fire. NOT built on native EventTarget: Node's own
// EventTarget.removeEventListener(type, fn, true) - the boolean capture
// form, exactly what the source's keydown listener uses - fails to match the
// listener added the same way (confirmed directly; the object form
// { capture: true } works fine). That's a Node runtime gap, not a bug in the
// source, which is correct per spec and works in real browsers - so this
// fake tracks listeners itself instead of delegating to Node's EventTarget.
type Listener = (event: Event) => void;

class FakeWindow {
  location = { href: "https://example.test/overlay" };
  private readonly listeners = new Map<string, Set<{ fn: Listener; capture: boolean }>>();

  addEventListener(
    type: string,
    fn: Listener,
    options?: boolean | { capture?: boolean },
  ): void {
    const capture = typeof options === "boolean" ? options : Boolean(options?.capture);
    const set = this.listeners.get(type) ?? new Set();
    set.add({ fn, capture });
    this.listeners.set(type, set);
  }

  removeEventListener(
    type: string,
    fn: Listener,
    options?: boolean | { capture?: boolean },
  ): void {
    const capture = typeof options === "boolean" ? options : Boolean(options?.capture);
    const set = this.listeners.get(type);
    if (!set) return;
    for (const entry of set) {
      if (entry.fn === fn && entry.capture === capture) {
        set.delete(entry);
      }
    }
  }

  dispatchEvent(event: Event): boolean {
    if (!Object.prototype.hasOwnProperty.call(event, "target")) {
      Object.defineProperty(event, "target", { value: this, configurable: true });
    }
    const set = this.listeners.get(event.type);
    if (set) {
      for (const entry of [...set]) {
        entry.fn(event);
      }
    }
    return true;
  }
}

// `instanceof HTMLElement` (and Input/TextArea/Select variants) in the
// source needs these names to exist as globals at all, or the comparison
// throws ReferenceError. A minimal fake with a working closest() (matching a
// fixed set of selectors handed to its constructor) is enough to drive every
// guard branch in handleKeyDown.
class FakeHTMLElement {
  isContentEditable = false;
  private readonly closestSelectors: Set<string>;
  constructor(closestSelectors: string[] = []) {
    this.closestSelectors = new Set(closestSelectors);
  }
  closest(selector: string): FakeHTMLElement | null {
    return this.closestSelectors.has(selector) ? this : null;
  }
}
class FakeHTMLInputElement extends FakeHTMLElement {
  value = "";
}
class FakeHTMLTextAreaElement extends FakeHTMLElement {}
class FakeHTMLSelectElement extends FakeHTMLElement {}

// Node has no KeyboardEvent global - build a plain Event and graft on the
// `key`/`target` properties the source reads, via defineProperty (Event's
// own `target` has no setter, but an own-property override on the instance
// still wins over the prototype getter, and survives dispatchEvent - verified
// directly: dispatchEvent only sets `target` to the dispatching EventTarget
// when the event doesn't already have one defined on it).
function buildKeydownEvent(key: string, target?: unknown): Event {
  const event = new Event("keydown", { cancelable: true, bubbles: true });
  Object.defineProperty(event, "key", { value: key, configurable: true });
  if (target !== undefined) {
    Object.defineProperty(event, "target", { value: target, configurable: true });
  }
  return event;
}

// useOverlayBackHandler() calls onMounted/onUnmounted - COMPONENT lifecycle
// hooks, not onScopeDispose. A bare effectScope() silently fails to register
// them (Vue only warns in dev), so the keydown/popstate listeners would never
// actually attach. Build a real, DOM-less component with a no-op custom
// renderer instead - same approach as useWorkflowRowStatus.test.ts.
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
let trackedDialogIds: symbol[] = [];
let pushStateMock: ReturnType<typeof vi.fn>;

function mountOverlayBackHandler(): App {
  const app = createApp({
    setup() {
      useOverlayBackHandler();
      return () => null;
    },
  });
  app.mount({});
  trackedApps.push(app);
  return app;
}

// dialogStack (from useDialogStack) is module-scoped shared state, same as
// in useDialogStack.test.ts - track every pushed id and drain it in
// afterEach so a thrown assertion doesn't leak an open dialog into later
// tests here or in useDialogStack's own test file.
function pushDialog(): symbol {
  const id = Symbol("dialog");
  trackedDialogIds.push(id);
  addToDialogStack(id);
  return id;
}

beforeEach(() => {
  vi.stubGlobal("window", new FakeWindow());
  vi.stubGlobal("document", {
    body: { dataset: {} as Record<string, string>, style: {} as Record<string, string> },
  });
  pushStateMock = vi.fn();
  vi.stubGlobal("history", { pushState: pushStateMock });
  vi.stubGlobal("HTMLElement", FakeHTMLElement);
  vi.stubGlobal("HTMLInputElement", FakeHTMLInputElement);
  vi.stubGlobal("HTMLTextAreaElement", FakeHTMLTextAreaElement);
  vi.stubGlobal("HTMLSelectElement", FakeHTMLSelectElement);
});

afterEach(() => {
  trackedApps.forEach((app) => app.unmount());
  trackedApps = [];
  trackedDialogIds.forEach((id) => removeFromDialogStack(id));
  trackedDialogIds = [];
  vi.unstubAllGlobals();
});

describe("dismissAllOverlays", () => {
  it("dispatches the DISMISS_OVERLAYS_EVENT custom event on window", () => {
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    dismissAllOverlays();

    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("pushOverlayState", () => {
  it("pushes a history state marking an overlay as open", () => {
    pushOverlayState();

    expect(pushStateMock).toHaveBeenCalledWith({ overlay: true }, "", window.location.href);
  });
});

describe("onDismissOverlays", () => {
  it("invokes the callback when the dismiss-overlays event fires", () => {
    const callback = vi.fn();
    onDismissOverlays(callback);

    dismissAllOverlays();

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("the returned unsubscribe function stops the callback from firing again", () => {
    const callback = vi.fn();
    const unsubscribe = onDismissOverlays(callback);
    unsubscribe();

    dismissAllOverlays();

    expect(callback).not.toHaveBeenCalled();
  });
});

describe("useOverlayBackHandler - Escape key", () => {
  it("dismisses all overlays on a plain Escape keydown", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Escape"));

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("ignores non-Escape keys", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Enter"));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when a dialog is open on the stack", () => {
    pushDialog();
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Escape"));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when the escape-trap data attribute is set", () => {
    document.body.dataset.heymOverlayEscapeTrap = "true";
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Escape"));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when a lightbox is open", () => {
    document.body.dataset.heymLightboxOpen = "true";
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Escape"));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when Escape is pressed inside a node-panel input", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);
    const target = new FakeHTMLInputElement([".node-panel"]);

    window.dispatchEvent(buildKeydownEvent("Escape", target));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when Escape is pressed inside an expression-output-query trap", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);
    const target = new FakeHTMLElement(["[data-heym-expression-query-trap]"]);

    window.dispatchEvent(buildKeydownEvent("Escape", target));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when Escape is pressed inside an inline-edit region", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);
    const target = new FakeHTMLElement(["[data-heym-inline-edit]"]);

    window.dispatchEvent(buildKeydownEvent("Escape", target));

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not dismiss overlays when Escape is pressed in a docs-sidebar input that has text", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);
    const target = new FakeHTMLInputElement([".docs-sidebar"]);
    target.value = "search query";

    window.dispatchEvent(buildKeydownEvent("Escape", target));

    expect(listener).not.toHaveBeenCalled();
  });

  it("DOES dismiss overlays when Escape is pressed in a docs-sidebar input that is empty", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);
    const target = new FakeHTMLInputElement([".docs-sidebar"]);
    target.value = "";

    window.dispatchEvent(buildKeydownEvent("Escape", target));

    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("useOverlayBackHandler - popstate", () => {
  it("dismisses all overlays on a popstate event", () => {
    mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(new Event("popstate"));

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("skips exactly one dismissal when the ignore-next-dismiss flag is set, then clears it", () => {
    mountOverlayBackHandler();
    document.body.dataset.heymIgnoreNextOverlayDismiss = "true";
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(new Event("popstate"));
    expect(listener).not.toHaveBeenCalled();
    expect(document.body.dataset.heymIgnoreNextOverlayDismiss).toBeUndefined();

    window.dispatchEvent(new Event("popstate"));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("useOverlayBackHandler - mount/unmount wiring", () => {
  it("does not respond to events before the component is mounted", () => {
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    window.dispatchEvent(buildKeydownEvent("Escape"));
    window.dispatchEvent(new Event("popstate"));

    expect(listener).not.toHaveBeenCalled();
  });

  it("stops responding to events after the component is unmounted", () => {
    const app = mountOverlayBackHandler();
    const listener = vi.fn();
    window.addEventListener(DISMISS_OVERLAYS_EVENT, listener);

    app.unmount();
    trackedApps = trackedApps.filter((tracked) => tracked !== app);

    window.dispatchEvent(buildKeydownEvent("Escape"));
    window.dispatchEvent(new Event("popstate"));

    expect(listener).not.toHaveBeenCalled();
  });
});
