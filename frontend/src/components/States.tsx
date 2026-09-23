import type { UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import type { ReactNode } from "react";
import { BACKEND_COMMAND, EMPTY, describeError } from "../lib/errors";

export function LoadingState({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={clsx("flex items-center gap-2 p-4 text-ink-muted", className)}>
      <span aria-hidden="true" className="size-3 animate-pulse rounded-full bg-ink-faint" />
      {label}
    </div>
  );
}

export function EmptyState({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <div role="status" className={clsx("rounded-md border border-dashed border-line-strong p-4 text-ink-muted", className)}>
      {children ?? EMPTY}
    </div>
  );
}

export function ErrorState({ error, className }: { error: unknown; className?: string }) {
  const info = describeError(error);
  return (
    <div
      role="alert"
      className={clsx(
        "rounded-md border p-4",
        info.kind === "no-data" ? "border-dashed border-line-strong text-ink-muted" : "border-danger text-ink",
        className,
      )}
    >
      <p className="font-medium">{info.title}</p>
      {info.kind === "unreachable" && (
        <code className="mt-2 block overflow-x-auto rounded bg-raised px-2 py-1 font-mono text-xs text-ink-muted">
          {BACKEND_COMMAND}
        </code>
      )}
      {info.detail && <p className="mt-1 text-xs text-ink-faint">{info.detail}</p>}
    </div>
  );
}

/** Shown above data that is still displayed after a background refetch failed. */
function StaleNote({ error }: { error: unknown }) {
  return (
    <p role="status" className="mb-2 text-xs text-ink-faint">
      Couldn’t refresh — showing the last data. {describeError(error).title}
    </p>
  );
}

type QueryViewProps<T> = {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
};

/** Loading, error, empty or data — every data view goes through this. */
export function QueryView<T>({ query, isEmpty, empty, loadingLabel, children }: QueryViewProps<T>) {
  if (query.data !== undefined) {
    return (
      <>
        {query.isError && <StaleNote error={query.error} />}
        {isEmpty?.(query.data) ? <EmptyState>{empty}</EmptyState> : children(query.data)}
      </>
    );
  }
  if (query.isError) return <ErrorState error={query.error} />;
  if (query.fetchStatus === "idle") return null;
  return <LoadingState label={loadingLabel} />;
}
