import clsx from "clsx";
import type { ReactNode } from "react";

const PATHS = {
  chart: (
    <>
      <path d="M7 3v4M7 17v4M17 4v3M17 15v5" />
      <rect x="5" y="7" width="4" height="10" rx="1" />
      <rect x="15" y="7" width="4" height="8" rx="1" />
    </>
  ),
  scanner: (
    <>
      <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />
      <path d="M7 9h10M7 12h7M7 15h4" />
    </>
  ),
  scorecard: <path d="m3 7 2 2 4-4M3 17l2 2 4-4M13 6h8M13 12h8M13 18h8" />,
  accuracy: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1" />
    </>
  ),
  news: (
    <>
      <path d="M4 5h12v14a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2z" />
      <path d="M16 9h4v10a2 2 0 0 1-2 2M8 9h4M8 13h4M8 17h2" />
    </>
  ),
  settings: (
    <>
      <path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" />
      <circle cx="15" cy="6" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="17" cy="18" r="2" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </>
  ),
  moon: <path d="M20 13.5A8 8 0 1 1 10.5 4a6.5 6.5 0 0 0 9.5 9.5z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </>
  ),
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  panelRight: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M15 4v16" />
    </>
  ),
  indicators: <path d="M3 12h4l3-7 4 14 3-7h4" />,
  patterns: (
    <>
      <path d="M4 17h6l-3-5z" />
      <path d="M14 7h6l-3 5z" />
    </>
  ),
  levels: <path d="M3 6h18M3 12h18M3 18h18" strokeDasharray="2 2.5" />,
  forecast: (
    <>
      <path d="M3 12h7" />
      <path d="M10 12 21 6M10 12l11 6" strokeDasharray="2 2" />
    </>
  ),
  keyboard: (
    <>
      <rect x="2.5" y="6" width="19" height="12" rx="2" />
      <path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9" />
    </>
  ),
  close: <path d="M18 6 6 18M6 6l12 12" />,
  plug: <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-5M12 8h.01" />
    </>
  ),
  pause: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 9v6M14 9v6" />
    </>
  ),
  trendUp: <path d="m3 17 6-6 4 4 8-8M15 7h6v6" />,
  trendDown: <path d="m3 7 6 6 4-4 8 8M21 11v6h-6" />,
  trendFlat: <path d="M3 12h18M17 8l4 4-4 4" />,
  wave: <path d="M2 12c2.5-5 4.5-5 7 0s4.5 5 7 0 3.5-4 6-1" />,
  level: (
    <>
      <path d="M3 12h5M16 12h5" strokeDasharray="2 2" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ),
  candle: (
    <>
      <path d="M12 3v4M12 17v4" />
      <rect x="8" y="7" width="8" height="10" rx="1" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="3" />,
  check: <path d="m5 12 5 5 9-10" />,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className, strokeWidth = 1.75 }: { name: IconName; className?: string; strokeWidth?: number }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={clsx("size-4 shrink-0", className)}
    >
      {PATHS[name]}
    </svg>
  );
}

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={clsx("size-6", className)}>
      <path d="M8 3v18" className="stroke-up" strokeWidth="1.5" />
      <rect x="5.5" y="7" width="5" height="9" rx="1" className="fill-up" />
      <path d="M16 2v18" className="stroke-down" strokeWidth="1.5" />
      <rect x="13.5" y="5" width="5" height="10" rx="1" className="fill-down" />
    </svg>
  );
}
