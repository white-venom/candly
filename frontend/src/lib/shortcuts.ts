import { useEffect, useLayoutEffect, useRef } from "react";

export type Hotkeys = Record<string, () => void>;

function typingInto(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/** Single-key shortcuts (by `KeyboardEvent.key`), ignored while typing or with Ctrl/Alt/Cmd held. */
export function useHotkeys(keys: Hotkeys, enabled = true): void {
  const latest = useRef(keys);
  useLayoutEffect(() => {
    latest.current = keys;
  });

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typingInto(e.target)) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      const action = latest.current[e.key];
      if (!action) return;
      e.preventDefault();
      action();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}

export const SHORTCUTS: { keys: string[]; action: string }[] = [
  { keys: ["/"], action: "Search instruments" },
  { keys: ["1", "2", "3", "4"], action: "Timeframe 5m · 15m · 1h · 1D" },
  { keys: ["[", "]"], action: "Previous / next instrument" },
  { keys: ["↑", "↓"], action: "Move through the watchlist" },
  { keys: ["i"], action: "Indicators" },
  { keys: ["t"], action: "Light / dark theme" },
  { keys: ["?"], action: "This help" },
  { keys: ["Esc"], action: "Close a menu or dialog" },
];
