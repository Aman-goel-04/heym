import { describe, expect, it } from "vitest";

import { previewRunStartsImmediately, shouldRunInsidePreviewSheet } from "@/lib/previewRun";

describe("preview run handoff", () => {
  it("keeps Run inside the preview when it is a bottom sheet", () => {
    expect(shouldRunInsidePreviewSheet(true)).toBe(true);
    expect(shouldRunInsidePreviewSheet(false)).toBe(false);
  });

  it("starts immediately only when the workflow has no input fields", () => {
    expect(previewRunStartsImmediately(0)).toBe(true);
    expect(previewRunStartsImmediately(1)).toBe(false);
    expect(previewRunStartsImmediately(3)).toBe(false);
  });
});
