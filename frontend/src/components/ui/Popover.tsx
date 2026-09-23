import clsx from "clsx";
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { focusFirst } from "./focus";

/**
 * A panel under its trigger. Render it next to the trigger inside a `relative` wrapper: a click
 * anywhere outside that wrapper, or Escape, closes it (Escape also returns focus to the trigger).
 */
const PLACEMENT = {
  "bottom-start": "top-full left-0 mt-2",
  "bottom-end": "top-full right-0 mt-2",
  "right-end": "left-full bottom-0 ml-3",
};

export function Popover({
  open,
  onClose,
  label,
  placement = "bottom-end",
  className,
  children,
}: {
  open: boolean;
  onClose: () => void;
  label: string;
  placement?: keyof typeof PLACEMENT;
  className?: string;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useLayoutEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    focusFirst(panel.current);
    const onPointer = (e: MouseEvent) => {
      const wrapper = panel.current?.parentElement;
      if (wrapper && !wrapper.contains(e.target as Node)) close.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      close.current();
      opener?.focus();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      ref={panel}
      role="dialog"
      aria-label={label}
      className={clsx("absolute z-40 rounded-lg border border-line bg-surface text-sm shadow-lg shadow-page/40", PLACEMENT[placement], className)}
    >
      {children}
    </div>
  );
}
