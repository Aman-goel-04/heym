import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BOARD_CARD_TOUCH_DRAG_EVENT,
  useBoardCardTouchDrag,
} from "./useBoardCardTouchDrag";
import type { BoardCardTouchDragDetail } from "./useBoardCardTouchDrag";

/**
 * No jsdom in this project's Vitest config, so `window` and the Touch/TouchEvent
 * classes don't exist. We only need the exact handful of calls the source makes:
 * addEventListener/removeEventListener/dispatchEvent on window, and a
 * touches/changedTouches collection exposing `.length` and `.item(i)`.
 */
function fakeTouchList(touches: Array<{ identifier: number; clientX: number; clientY: number }>) {
  return {
    length: touches.length,
    item: (index: number) => touches[index] ?? null,
  };
}

function fakeTouchEvent(options: {
  touches?: Array<{ identifier: number; clientX: number; clientY: number }>;
  changedTouches?: Array<{ identifier: number; clientX: number; clientY: number }>;
  target?: unknown;
  cancelable?: boolean;
}) {
  return {
    touches: fakeTouchList(options.touches ?? []),
    changedTouches: fakeTouchList(options.changedTouches ?? options.touches ?? []),
    target: options.target ?? null,
    cancelable: options.cancelable ?? true,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as TouchEvent;
}

let dispatched: BoardCardTouchDragDetail[];
let addEventListener: ReturnType<typeof vi.fn>;
let removeEventListener: ReturnType<typeof vi.fn>;

function stubWindow(): void {
  dispatched = [];
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  vi.stubGlobal("window", {
    addEventListener,
    removeEventListener,
    dispatchEvent: vi.fn((event: CustomEvent<BoardCardTouchDragDetail>) => {
      if (event.type === BOARD_CARD_TOUCH_DRAG_EVENT) dispatched.push(event.detail);
    }),
  });
  // CustomEvent and Element aren't available in a plain Node environment either.
  vi.stubGlobal(
    "CustomEvent",
    class FakeCustomEvent<T> {
      type: string;
      detail: T;
      constructor(type: string, init: { detail: T }) {
        this.type = type;
        this.detail = init.detail;
      }
    },
  );
  vi.stubGlobal("Element", class FakeElement {});
}

function setup(overrides?: { enabled?: () => boolean; closest?: ReturnType<typeof vi.fn> }) {
  const closest = overrides?.closest ?? vi.fn(() => null);
  const sourceElement = { value: { closest } };
  const drag = useBoardCardTouchDrag({
    cardId: () => "card-1",
    enabled: overrides?.enabled ?? (() => true),
    sourceElement: sourceElement as never,
  });
  return { ...drag, closest };
}

beforeEach(() => {
  vi.useFakeTimers();
  stubWindow();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("onTouchStart", () => {
  it("does nothing and never starts a long-press timer when disabled", () => {
    const { handlers, isTouchDragging } = setup({ enabled: () => false });

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(1000);

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("does nothing when more than one finger is already down", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({
        touches: [
          { identifier: 1, clientX: 0, clientY: 0 },
          { identifier: 2, clientX: 5, clientY: 5 },
        ],
      }),
    );
    vi.advanceTimersByTime(1000);

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("ignores touches starting on an interactive element, without even arming a cancel", () => {
    const { handlers, isTouchDragging } = setup();
    const button = { closest: vi.fn(() => ({})) };
    Object.setPrototypeOf(button, Element.prototype);

    handlers.onTouchStart(
      fakeTouchEvent({
        touches: [{ identifier: 1, clientX: 0, clientY: 0 }],
        target: button,
      }),
    );
    vi.advanceTimersByTime(1000);

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("does not start dragging before the long-press threshold elapses", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 10, clientY: 20 }] }),
    );
    vi.advanceTimersByTime(299);

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("starts dragging and dispatches a start event once the long-press threshold elapses", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 10, clientY: 20 }] }),
    );
    vi.advanceTimersByTime(300);

    expect(isTouchDragging.value).toBe(true);
    expect(dispatched).toEqual([
      { phase: "start", cardId: "card-1", clientX: 10, clientY: 20 },
    ]);
  });

  it("cancels an in-flight long-press and starts a fresh one when a second touchstart arrives", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    // A second touchstart on window, routed through the captured
    // onAdditionalTouchStart listener, with more than one finger now down.
    expect(addEventListener).toHaveBeenCalledWith(
      "touchstart",
      expect.any(Function),
      { capture: true },
    );
    const additionalListener = addEventListener.mock.calls[0][1] as (e: TouchEvent) => void;
    additionalListener(
      fakeTouchEvent({
        touches: [
          { identifier: 1, clientX: 0, clientY: 0 },
          { identifier: 2, clientX: 1, clientY: 1 },
        ],
      }),
    );

    // The first gesture never got a chance to fire its long-press timer.
    vi.advanceTimersByTime(300);
    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);

    // A brand-new touchstart (single finger) starts a clean gesture.
    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 3, clientX: 5, clientY: 5 }] }),
    );
    vi.advanceTimersByTime(300);
    expect(isTouchDragging.value).toBe(true);
    expect(dispatched).toEqual([
      { phase: "start", cardId: "card-1", clientX: 5, clientY: 5 },
    ]);
  });
});

describe("onTouchMove", () => {
  it("does nothing when there is no active gesture", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchMove(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("resets silently, without a cancel event, when movement exceeds tolerance before becoming a drag", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    handlers.onTouchMove(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 50, clientY: 0 }] }),
    );

    // The long-press timer would have fired had the gesture not reset.
    vi.advanceTimersByTime(300);

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("cancels (with a cancel event) when a second finger joins mid-drag", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);
    expect(isTouchDragging.value).toBe(true);

    handlers.onTouchMove(
      fakeTouchEvent({
        touches: [
          { identifier: 1, clientX: 1, clientY: 1 },
          { identifier: 2, clientX: 2, clientY: 2 },
        ],
      }),
    );

    // cancelGesture() fires before this move event's point is recorded, so it
    // reports the still-stale latestPoint from touchstart (0, 0), not (1, 1).
    expect(isTouchDragging.value).toBe(false);
    expect(dispatched.at(-1)).toEqual({
      phase: "cancel",
      cardId: "card-1",
      clientX: 0,
      clientY: 0,
    });
  });

  it("cancels when the tracked finger's identifier disappears", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);

    handlers.onTouchMove(
      fakeTouchEvent({ touches: [{ identifier: 99, clientX: 1, clientY: 1 }] }),
    );

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched.at(-1)?.phase).toBe("cancel");
  });

  it("prevents default, scrolls the edge, and dispatches move while actively dragging", () => {
    const closest = vi.fn(() => ({
      getBoundingClientRect: () => ({ left: 0, right: 1000 }),
      scrollLeft: 100,
    }));
    const { handlers, isTouchDragging } = setup({ closest });

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);
    expect(isTouchDragging.value).toBe(true);

    const moveEvent = fakeTouchEvent({
      touches: [{ identifier: 1, clientX: 10, clientY: 500 }],
    });
    handlers.onTouchMove(moveEvent);

    expect(moveEvent.preventDefault).toHaveBeenCalledTimes(1);
    expect(dispatched.at(-1)).toEqual({
      phase: "move",
      cardId: "card-1",
      clientX: 10,
      clientY: 500,
    });
  });

  it("does not call preventDefault when the move event is not cancelable", () => {
    const { handlers } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);

    const moveEvent = fakeTouchEvent({
      touches: [{ identifier: 1, clientX: 10, clientY: 10 }],
      cancelable: false,
    });
    handlers.onTouchMove(moveEvent);

    expect(moveEvent.preventDefault).not.toHaveBeenCalled();
  });
});

describe("scrollBoardAtEdge (exercised through onTouchMove)", () => {
  function dragTo(clientX: number, closest: ReturnType<typeof vi.fn>) {
    const { handlers } = setup({ closest });
    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);
    handlers.onTouchMove(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX, clientY: 0 }] }),
    );
  }

  it("does not throw and does not scroll when no board canvas is found", () => {
    const closest = vi.fn(() => null);
    expect(() => dragTo(5, closest)).not.toThrow();
  });

  it("scrolls left when the point is within the left edge zone", () => {
    const canvas = {
      getBoundingClientRect: () => ({ left: 0, right: 1000 }),
      scrollLeft: 100,
    };
    dragTo(10, vi.fn(() => canvas));
    expect(canvas.scrollLeft).toBe(82);
  });

  it("scrolls right when the point is within the right edge zone", () => {
    const canvas = {
      getBoundingClientRect: () => ({ left: 0, right: 1000 }),
      scrollLeft: 100,
    };
    dragTo(990, vi.fn(() => canvas));
    expect(canvas.scrollLeft).toBe(118);
  });

  it("does not scroll when the point is away from both edges", () => {
    const canvas = {
      getBoundingClientRect: () => ({ left: 0, right: 1000 }),
      scrollLeft: 100,
    };
    dragTo(500, vi.fn(() => canvas));
    expect(canvas.scrollLeft).toBe(100);
  });
});

describe("onTouchEnd", () => {
  it("does nothing when there is no active gesture", () => {
    const { handlers } = setup();

    handlers.onTouchEnd(fakeTouchEvent({ changedTouches: [] }));

    expect(dispatched).toHaveLength(0);
  });

  it("just resets, without an end event, if the long press never completed", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    handlers.onTouchEnd(
      fakeTouchEvent({ changedTouches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  it("cancels if the tracked touch is missing from changedTouches while dragging", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);

    handlers.onTouchEnd(
      fakeTouchEvent({ changedTouches: [{ identifier: 99, clientX: 0, clientY: 0 }] }),
    );

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched.at(-1)?.phase).toBe("cancel");
  });

  it("dispatches an end event, suppresses the next click, and resets on a normal end", () => {
    const { handlers, isTouchDragging, consumeTouchDragClick } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);

    const endEvent = fakeTouchEvent({
      changedTouches: [{ identifier: 1, clientX: 7, clientY: 8 }],
    });
    handlers.onTouchEnd(endEvent);

    expect(endEvent.preventDefault).toHaveBeenCalledTimes(1);
    expect(endEvent.stopPropagation).toHaveBeenCalledTimes(1);
    expect(dispatched.at(-1)).toEqual({
      phase: "end",
      cardId: "card-1",
      clientX: 7,
      clientY: 8,
    });
    expect(isTouchDragging.value).toBe(false);
    expect(consumeTouchDragClick()).toBe(true);
  });
});

describe("onTouchCancel", () => {
  it("dispatches a cancel event when a drag was in progress", () => {
    const { handlers, isTouchDragging } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 3, clientY: 4 }] }),
    );
    vi.advanceTimersByTime(300);

    handlers.onTouchCancel(fakeTouchEvent({}));

    expect(isTouchDragging.value).toBe(false);
    expect(dispatched.at(-1)).toEqual({
      phase: "cancel",
      cardId: "card-1",
      clientX: 3,
      clientY: 4,
    });
  });

  it("dispatches nothing when no drag was in progress", () => {
    const { handlers } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    // Long press hasn't fired yet, so isTouchDragging is still false.
    handlers.onTouchCancel(fakeTouchEvent({}));

    expect(dispatched).toHaveLength(0);
  });

  it("always removes the additional-touchstart listener, even when no drag was in progress", () => {
    const { handlers } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    removeEventListener.mockClear();

    handlers.onTouchCancel(fakeTouchEvent({}));

    expect(removeEventListener).toHaveBeenCalledWith(
      "touchstart",
      expect.any(Function),
      { capture: true },
    );
  });
});

describe("consumeTouchDragClick", () => {
  it("returns false when there is nothing to suppress", () => {
    const { consumeTouchDragClick } = setup();

    expect(consumeTouchDragClick()).toBe(false);
  });

  it("returns true exactly once for a suppressed click, then false on the next call", () => {
    const { handlers, consumeTouchDragClick } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);
    handlers.onTouchEnd(
      fakeTouchEvent({ changedTouches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );

    expect(consumeTouchDragClick()).toBe(true);
    expect(consumeTouchDragClick()).toBe(false);
  });

  it("returns false once the suppression window has elapsed", () => {
    const { handlers, consumeTouchDragClick } = setup();

    handlers.onTouchStart(
      fakeTouchEvent({ touches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );
    vi.advanceTimersByTime(300);
    handlers.onTouchEnd(
      fakeTouchEvent({ changedTouches: [{ identifier: 1, clientX: 0, clientY: 0 }] }),
    );

    vi.advanceTimersByTime(701);

    expect(consumeTouchDragClick()).toBe(false);
  });
});
