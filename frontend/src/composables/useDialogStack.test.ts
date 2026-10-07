import { afterEach, beforeEach, vi, describe, expect, it } from "vitest";
import { effectScope, nextTick, ref, type ComputedRef, type EffectScope } from "vue";
import {
  addToDialogStack as rawAddToDialogStack,
  dialogStackPosition,
  hasOpenDialog,
  isBottommostDialog,
  isTopmostDialog,
  overlayZIndex,
  removeFromDialogStack,
  useDialogStackLayer,
} from "./useDialogStack";

// `dialogStack` is module-scoped, so a thrown assertion mid-test (proven by
// mutation-testing the dedup logic - breaking it cascaded one failure into
// five) skips any manual removeFromDialogStack cleanup written at the bottom
// of a test body. Route every push through this tracker and every
// effectScope through trackScope instead, and the afterEach below drains both
// unconditionally, independent of whether the test body finished or threw.
let trackedIds: symbol[] = [];
let trackedScopes: EffectScope[] = [];

function addToDialogStack(dialogId: symbol): void {
  trackedIds.push(dialogId);
  rawAddToDialogStack(dialogId);
}

function trackScope(scope: EffectScope): EffectScope {
  trackedScopes.push(scope);
  return scope;
}

beforeEach(() => {
  vi.stubGlobal("document", { body: { style: {} } });
});

afterEach(() => {
  trackedScopes.forEach((scope) => scope.stop());
  trackedScopes = [];
  trackedIds.forEach((id) => removeFromDialogStack(id));
  trackedIds = [];
});

describe("addToDialogStack / removeFromDialogStack / isTopmostDialog", () => {
  it("tracks the most recently pushed dialog as topmost", () => {
    const first = Symbol("first")
    const second = Symbol("second")
    addToDialogStack(first)
    expect(isTopmostDialog(first)).toBe(true)
    addToDialogStack(second)
    expect(isTopmostDialog(second)).toBe(true)
    expect(isTopmostDialog(first)).toBe(false)
  });
  
  // TODO: push a dialog, push it AGAIN (same symbol) - it should move to the
  // top rather than appear twice. Check dialogStackPosition / isTopmostDialog
  // reflect one occurrence, not two.
  it("re-pushing an already-open dialog moves it to the top instead of duplicating it", () => {
    const first = Symbol("first");
    const second = Symbol("second");
    addToDialogStack(first);
    addToDialogStack(second);
    addToDialogStack(first);

    expect(dialogStackPosition(first)).toBe(1);
    expect(isTopmostDialog(first)).toBe(true);
    expect(isTopmostDialog(second)).toBe(false);
  });
  
  // TODO: push a dialog, remove it, then check isTopmostDialog/isBottommostDialog
  // both return false for it, and hasOpenDialog() reflects whether anything
  // else is still in the stack.
  it("removing a dialog drops it out of top/bottom checks", () => {
    const first = Symbol("first");
    addToDialogStack(first);
    removeFromDialogStack(first);
  
    expect(isTopmostDialog(first)).toBe(false);
    expect(isBottommostDialog(first)).toBe(false);
  });

  // TODO: removeFromDialogStack on an id that was never pushed should not
  // throw and should not affect the current stack state.
  it("removing an id that was never pushed is a no-op", () => {
    const first = Symbol("first");
    removeFromDialogStack(first);
  });
});

describe("isBottommostDialog / dialogStackPosition", () => {
  // TODO: push three symbols, the FIRST one pushed should be bottommost
  // (isBottommostDialog === true), not the others. dialogStackPosition should
  // return 0 for it, 1 and 2 for the next two in push order.
  it("the first dialog pushed is bottommost, later ones have increasing position", () => {
    const first = Symbol("first");
    const second = Symbol("second");
    const third = Symbol("third");
    addToDialogStack(first);
    addToDialogStack(second);
    addToDialogStack(third);

    expect(isBottommostDialog(first)).toBe(true);
    expect(isBottommostDialog(second)).toBe(false);
    expect(isBottommostDialog(third)).toBe(false);
    expect(dialogStackPosition(first)).toBe(0);
    expect(dialogStackPosition(second)).toBe(1);
    expect(dialogStackPosition(third)).toBe(2);
  });

  // TODO: dialogStackPosition for an id never pushed should return 0 (per
  // the Math.max(...indexOf(...), 0) fallback), not -1 or throw.
  it("dialogStackPosition for an unknown id falls back to 0, not -1", () => {
    const unknown = Symbol("unknown");

    expect(dialogStackPosition(unknown)).toBe(0);
  });
});

describe("overlayZIndex", () => {
  // TODO: push two symbols, assert the z-index of the second is exactly
  // OVERLAY_Z_INDEX_STEP (10) higher than the first's, and the first's is
  // OVERLAY_BASE_Z_INDEX (50) - these constants aren't exported, so hardcode
  // 50/10 in the test per the source's own values, or recompute the expected
  // z-index from dialogStackPosition() so the test tracks the source if the
  // constants ever change.
  it("z-index increases by one step per stack position, based at 50", () => {
    const first = Symbol("first");
    const second = Symbol("second");
    addToDialogStack(first);
    addToDialogStack(second);

    expect(overlayZIndex(first)).toBe(50);
    expect(overlayZIndex(second)).toBe(60);
  });
});

describe("useDialogStackLayer", () => {
  // TODO: this is a composable, so it needs a reactive host - mount it with
  // Vue's effectScope() (import { effectScope } from "vue") rather than a
  // full component, since there's no template here. Something like:
  //   const scope = effectScope();
  //   const isOpen = ref(false);
  //   let layerZ: ComputedRef<number>;
  //   scope.run(() => { layerZ = useDialogStackLayer(() => isOpen.value); });
  // Because `watch(isOpen, ..., { immediate: true })` runs synchronously on
  // call for the immediate invocation, but LATER changes to isOpen.value are
  // only picked up on Vue's next reactivity flush - you'll likely need
  // `await nextTick()` (import from "vue") after flipping isOpen.value before
  // asserting the stack changed.
  //
  // Set isOpen.value = true, await nextTick(), confirm hasOpenDialog() is now
  // true (immediate: true means it should actually join on creation, when
  // isOpen() starts out true - check that case directly by constructing the
  // layer with the open-returning function in the first place, no flip
  // needed. Then test setting isOpen.value = false, await nextTick(), confirm
  // it dropped out of the stack).
  it("joining the layer while isOpen() is true adds it to the dialog stack immediately", async () => {
    expect(hasOpenDialog()).toBe(false);
    const scope = trackScope(effectScope());
    const isOpen = ref(true);
    let layerZ!: ComputedRef<number>;
    scope.run(() => {
      layerZ = useDialogStackLayer(() => isOpen.value);
    });

    await nextTick();

    expect(hasOpenDialog()).toBe(true);
    expect(layerZ.value).toBe(50);
  });

  // TODO: same setup, but construct with isOpen starting false, flip to true,
  // nextTick, confirm it joined; flip back to false, nextTick, confirm it
  // left.
  it("toggling the isOpen source adds/removes the layer from the stack reactively", async () => {
    const scope = trackScope(effectScope());
    const isOpen = ref(false);
    scope.run(() => {
      useDialogStackLayer(() => isOpen.value);
    });

    await nextTick();
    expect(hasOpenDialog()).toBe(false);

    isOpen.value = true;
    await nextTick();
    expect(hasOpenDialog()).toBe(true);

    isOpen.value = false;
    await nextTick();
    expect(hasOpenDialog()).toBe(false);
  });

  // TODO: scope.stop() should trigger onScopeDispose, which should remove the
  // layer even if isOpen was never flipped back to false manually. Create the
  // layer with isOpen starting true, confirm it's in the stack, call
  // scope.stop(), confirm hasOpenDialog() reflects it leaving (or, if other
  // dialogs are still open from stack bleed, check isTopmostDialog/whatever
  // specific check proves THIS id left - this is exactly why state cleanup
  // between tests matters for this file).
  it("disposing the owning scope removes the layer from the stack", async () => {
    const scope = trackScope(effectScope());
    const isOpen = ref(true);
    scope.run(() => {
      useDialogStackLayer(() => isOpen.value);
    });

    await nextTick();
    expect(hasOpenDialog()).toBe(true);

    scope.stop();

    expect(hasOpenDialog()).toBe(false);
  });
});
