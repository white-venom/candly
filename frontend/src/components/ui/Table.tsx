import clsx from "clsx";
import type { ReactNode } from "react";
import type { SortDir } from "../../lib/sort";

/** A bordered, horizontally scrollable table frame with a sticky header row. */
export function TableFrame({ children, className, label }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <div className={clsx("overflow-x-auto rounded-lg border border-line bg-surface", className)}>
      <table aria-label={label} className="w-full border-collapse text-sm tabular-nums">
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  numeric,
  sort,
  onSort,
  className,
  title,
}: {
  children?: ReactNode;
  numeric?: boolean;
  /** this column's current sort direction, or null when another column sorts */
  sort?: SortDir | null;
  onSort?: () => void;
  className?: string;
  title?: string;
}) {
  const label = onSort ? (
    <button
      type="button"
      onClick={onSort}
      title={title}
      className={clsx("inline-flex items-center gap-1 rounded uppercase transition-colors hover:text-ink", sort && "text-ink", numeric && "flex-row-reverse")}
    >
      {children}
      <span aria-hidden="true" className={sort ? "text-accent" : "text-transparent"}>
        {sort === "asc" ? "↑" : "↓"}
      </span>
    </button>
  ) : (
    <span title={title}>{children}</span>
  );
  return (
    <th
      scope="col"
      aria-sort={sort ? (sort === "asc" ? "ascending" : "descending") : undefined}
      className={clsx(
        "sticky top-0 z-10 h-9 border-b border-line bg-surface px-3 text-2xs font-semibold tracking-wider whitespace-nowrap text-ink-faint uppercase",
        numeric ? "text-right" : "text-left",
        className,
      )}
    >
      {label}
    </th>
  );
}

export function Td({ children, numeric, className, title }: { children?: ReactNode; numeric?: boolean; className?: string; title?: string }) {
  return (
    <td title={title} className={clsx("h-10 border-b border-line/60 px-3 whitespace-nowrap", numeric && "text-right", className)}>
      {children}
    </td>
  );
}

export function Dash() {
  return <span className="text-ink-faint">—</span>;
}
