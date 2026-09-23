import clsx from "clsx";
import type { ReactNode } from "react";

const SIDE = {
  right: "left-full top-1/2 ml-2 -translate-y-1/2",
  bottom: "top-full left-1/2 mt-2 -translate-x-1/2",
  "bottom-end": "top-full right-0 mt-2",
  top: "bottom-full left-1/2 mb-2 -translate-x-1/2",
} as const;

export type TipSide = keyof typeof SIDE;

/**
 * A hover/keyboard-focus label. It repeats the control's accessible name, so it is hidden from
 * assistive tech; the control itself must carry the name (aria-label).
 */
export function Tip({ label, side = "bottom", children, className }: { label: ReactNode; side?: TipSide; children: ReactNode; className?: string }) {
  return (
    <span className={clsx("group/tip relative inline-flex", className)}>
      {children}
      <span
        aria-hidden="true"
        className={clsx(
          "pointer-events-none absolute z-50 w-max max-w-64 rounded-md border border-line bg-raised px-2 py-1 text-xs leading-4 font-normal text-ink opacity-0 shadow-sm transition-opacity",
          "group-hover/tip:opacity-100 group-hover/tip:delay-300 group-has-[:focus-visible]/tip:opacity-100",
          SIDE[side],
        )}
      >
        {label}
      </span>
    </span>
  );
}
