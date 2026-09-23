import { useEffect, useState } from "react";
import { useHealth } from "../../api/hooks";
import { readStorage, writeStorage } from "../../lib/storage";
import { nowUnix } from "../../lib/time";
import { IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";

const SEEN_KEY = "candly.syncDone";
const RECENT = 24 * 3600;
const SHOW_MS = 8000;

/** A one-time "setup complete" note when the Fyers first sync finishes. */
export function SyncToast() {
  const sync = useHealth().data?.sync;
  const finished = sync?.status === "done" ? sync.finished_at : null;
  const [seen] = useState(() => readStorage(SEEN_KEY));
  const [dismissed, setDismissed] = useState<number | null>(null);
  const show = finished !== null && nowUnix() - finished <= RECENT && seen !== String(finished) && dismissed !== finished;

  useEffect(() => {
    if (!show || finished === null) return;
    writeStorage(SEEN_KEY, String(finished));
    const timer = window.setTimeout(() => setDismissed(finished), SHOW_MS);
    return () => window.clearTimeout(timer);
  }, [show, finished]);

  if (!show) return null;
  return (
    <div role="status" className="fixed right-4 bottom-4 z-50 flex w-80 items-start gap-3 rounded-lg border border-line bg-surface p-3 text-sm shadow-lg shadow-page/40">
      <span className="mt-0.5 flex size-5 items-center justify-center rounded-full bg-up/15 text-up">
        <Icon name="check" className="size-3.5" strokeWidth={2.5} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-ink">Fyers setup complete</p>
        <p className="text-xs text-ink-muted">History, levels and scorecards are ready. Live data is flowing.</p>
      </div>
      <IconButton icon="close" label="Dismiss" tip={null} size="sm" onClick={() => setDismissed(finished)} className="-mt-1 -mr-1" />
    </div>
  );
}
