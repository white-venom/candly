import type { UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import type { ReactNode } from "react";
import { friendlyError } from "../../lib/messages";
import { Icon, type IconName } from "./Icon";

export function LoadingState({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={clsx("flex items-center justify-center gap-2 p-6 text-sm text-ink-muted", className)}>
      <span aria-hidden="true" className="size-3.5 animate-spin rounded-full border-2 border-line border-t-ink-muted" />
      {label}
    </div>
  );
}

export type EmptyInfo = { title: string; hint?: ReactNode };

const NO_DATA: EmptyInfo = { title: "No data yet", hint: "Run ingest to load it." };

/** A calm centred message: icon, one line, one hint, an optional action. */
export function EmptyState({
  title,
  hint,
  icon = "info",
  action,
  className,
  role = "status",
}: EmptyInfo & { icon?: IconName; action?: ReactNode; className?: string; role?: "status" | "alert" }) {
  return (
    <div role={role} className={clsx("flex flex-col items-center justify-center gap-2 p-8 text-center", className)}>
      <span className="mb-1 flex size-9 items-center justify-center rounded-full bg-raised text-ink-muted">
        <Icon name={icon} className="size-[18px]" />
      </span>
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <div className="max-w-lg text-xs text-ink-muted">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, noDataHint, className, action }: { error: unknown; noDataHint?: string; className?: string; action?: ReactNode }) {
  const f = friendlyError(error, noDataHint);
  return (
    <EmptyState
      role="alert"
      icon={f.kind === "no-data" ? "info" : f.kind === "unreachable" ? "plug" : "pause"}
      title={f.title}
      className={className}
      action={action}
      hint={
        (f.hint || f.command) && (
          <>
            {f.hint}
            {f.command && (
              <code className="mt-2 block rounded-md border border-line bg-page px-2 py-1.5 text-left font-mono text-2xs break-words text-ink-muted select-all">
                {f.command}
              </code>
            )}
          </>
        )
      }
    />
  );
}

type QueryViewProps<T> = {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  empty?: EmptyInfo;
  loadingLabel?: string;
  noDataHint?: string;
  className?: string;
  children: (data: T) => ReactNode;
};

/** Loading, error, empty or data — every data view goes through this. */
export function QueryView<T>({ query, isEmpty, empty = NO_DATA, loadingLabel, noDataHint, className, children }: QueryViewProps<T>) {
  if (query.data !== undefined) {
    if (isEmpty?.(query.data)) return <EmptyState {...empty} className={className} />;
    return (
      <>
        {query.isError && (
          <p role="status" className="px-3 py-1 text-2xs text-ink-faint">
            Couldn’t refresh — showing the last data. {friendlyError(query.error).title}.
          </p>
        )}
        {children(query.data)}
      </>
    );
  }
  if (query.isError) return <ErrorState error={query.error} noDataHint={noDataHint} className={className} />;
  if (query.fetchStatus === "idle") return null;
  return <LoadingState label={loadingLabel} className={className} />;
}
