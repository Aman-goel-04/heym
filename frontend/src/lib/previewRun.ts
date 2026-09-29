/**
 * The quick drawer is a desktop side panel. While the preview is a bottom sheet,
 * Run has to stay there — closing the sheet to open the drawer leaves the run nowhere to land.
 */
export function shouldRunInsidePreviewSheet(previewIsSheet: boolean): boolean {
  return previewIsSheet;
}

/**
 * A workflow with input fields waits for values. One with none starts immediately,
 * the same way the desktop drawer is ready to run without a form.
 */
export function previewRunStartsImmediately(inputFieldCount: number): boolean {
  return inputFieldCount === 0;
}
