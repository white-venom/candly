const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)];
}

/** Focuses `[data-autofocus]`, else the first field, else the first control that isn't a close button. */
export function focusFirst(root: HTMLElement | null): void {
  if (!root) return;
  const target =
    root.querySelector<HTMLElement>("[data-autofocus]") ??
    root.querySelector<HTMLElement>("input:not(:disabled), select:not(:disabled), textarea:not(:disabled)") ??
    focusables(root).find((el) => !el.hasAttribute("data-close")) ??
    root;
  target.focus();
}

/** Keeps Tab inside `root`. */
export function trapTab(e: { key: string; shiftKey: boolean; preventDefault: () => void }, root: HTMLElement | null): void {
  if (e.key !== "Tab" || !root) return;
  const items = focusables(root);
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}
