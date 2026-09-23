import { useHealth, useStartSync } from "../../api/hooks";
import { dataStatus, expiryNotice, friendlyError, syncNotice } from "../../lib/messages";
import { useShell } from "../../lib/shell";
import { Icon } from "../ui/Icon";

/**
 * Slim lines under a page's top bar: the Fyers first sync (progress, or a stop with Retry), live data
 * paused, or an expiry schedule that changed.
 */
export function NoticeStrip() {
  const health = useHealth();
  const retry = useStartSync();
  const { openConnect } = useShell();
  const status = dataStatus(health.data, health.error);
  const sync = syncNotice(health.data);
  const expiry = expiryNotice(health.data);
  const paused = !sync && status.state === "paused";
  if (!sync && !paused && !expiry) return null;

  return (
    <div role="status" aria-label="Notices" className="shrink-0 divide-y divide-line border-b border-line text-xs text-ink">
      {sync?.state === "running" && (
        <div className="relative bg-accent/8">
          <p className="flex h-8 items-center gap-2 px-3">
            <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
            <span className="truncate">{sync.text}</span>
          </p>
          {sync.progress !== null && (
            <div
              role="progressbar"
              aria-label="Fyers setup"
              aria-valuenow={sync.progress}
              aria-valuemin={0}
              aria-valuemax={100}
              className="absolute inset-x-0 bottom-0 h-0.5 bg-accent/15"
            >
              <div className="h-full bg-accent transition-[width] duration-700" style={{ width: `${sync.progress}%` }} />
            </div>
          )}
        </div>
      )}
      {sync?.state === "error" && (
        <p className="flex h-8 items-center gap-2 bg-forming/8 px-3">
          <Icon name="pause" className="size-3.5 text-forming" />
          <span className="truncate">{sync.text}</span>
          <button
            type="button"
            onClick={() => retry.mutate()}
            disabled={retry.isPending}
            className="shrink-0 rounded font-medium text-accent hover:underline disabled:opacity-60"
          >
            {retry.isPending ? "Retrying…" : "Retry"}
          </button>
          {retry.isError && <span className="truncate text-ink-muted">{friendlyError(retry.error).title}</span>}
        </p>
      )}
      {paused && (
        <p className="flex h-8 items-center gap-2 bg-forming/8 px-3">
          <Icon name="pause" className="size-3.5 text-forming" />
          <span className="truncate">
            {status.title}
            {status.detail && <span className="text-ink-muted"> — {status.detail}</span>}
          </span>
          {status.canConnect && (
            <>
              <span aria-hidden="true" className="text-ink-faint">
                ·
              </span>
              <button type="button" onClick={openConnect} className="shrink-0 rounded font-medium text-accent hover:underline">
                Connect
              </button>
            </>
          )}
        </p>
      )}
      {expiry && (
        <p className="flex h-8 items-center gap-2 bg-forming/8 px-3">
          <Icon name="calendar" className="size-3.5 text-forming" />
          <span className="truncate">{expiry}</span>
        </p>
      )}
    </div>
  );
}
