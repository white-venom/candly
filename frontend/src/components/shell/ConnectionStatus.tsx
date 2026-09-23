import clsx from "clsx";
import { useState } from "react";
import { useFyersStatus, useHealth } from "../../api/hooks";
import { BACKEND_COMMAND, dataStatus, type DataStatus } from "../../lib/messages";
import { useShell } from "../../lib/shell";
import { formatWhenIST } from "../../lib/time";
import { Button } from "../ui/Button";
import { Popover } from "../ui/Popover";
import { Tip } from "../ui/Tooltip";

const DOT: Record<DataStatus["state"], string> = {
  live: "bg-up",
  syncing: "bg-accent animate-pulse",
  paused: "bg-forming",
  offline: "bg-danger",
  checking: "bg-ink-faint",
};

function statusLine(s: DataStatus): string {
  return s.detail ? `${s.title} — ${s.detail}` : s.title;
}

/** The rail's data dot: green live, amber paused, red offline. Click for details and the Fyers login. */
export function ConnectionStatus() {
  const health = useHealth();
  const status = dataStatus(health.data, health.error);
  const connected = health.data?.keys.fyers === true && health.data.keys.fyers_connected;
  const fyers = useFyersStatus(connected);
  const { openConnect } = useShell();
  const [open, setOpen] = useState(false);

  const expires = fyers.data?.connected && fyers.data.expires_at ? formatWhenIST(fyers.data.expires_at) : null;

  return (
    <div className="relative">
      <Tip label={statusLine(status)} side="right">
        <button
          type="button"
          aria-label={`Data connection: ${statusLine(status)}`}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-raised"
        >
          <span className={clsx("size-2.5 rounded-full ring-4", DOT[status.state], status.state === "live" ? "ring-up/15" : status.state === "paused" ? "ring-forming/15" : "ring-transparent")} />
        </button>
      </Tip>
      <Popover open={open} onClose={() => setOpen(false)} label="Data connection" placement="right-end" className="w-72 p-3">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className={clsx("size-2 rounded-full", DOT[status.state])} />
          <p className="font-medium text-ink">{status.title}</p>
        </div>
        {status.detail && <p className="mt-1 text-xs text-ink-muted">{status.detail}</p>}
        {status.state === "offline" && (
          <code className="mt-2 block rounded-md border border-line bg-page px-2 py-1.5 font-mono text-2xs break-all text-ink-muted">{BACKEND_COMMAND}</code>
        )}
        {health.data && (
          <ul aria-label="Market sessions" className="mt-3 flex flex-col gap-1 border-t border-line pt-3 text-xs">
            {health.data.markets.map((m) => (
              <li key={m.exchange} className="flex items-center gap-2">
                <span aria-hidden="true" className={clsx("size-1.5 rounded-full", m.open ? "bg-up" : "bg-line-strong")} />
                <span className="w-9 font-medium text-ink">{m.exchange}</span>
                <span className="text-ink-muted">{m.open ? "Open" : "Closed"}</span>
              </li>
            ))}
            {expires && <li className="pt-1 text-ink-muted">Fyers session until {expires} IST</li>}
          </ul>
        )}
        {status.canConnect && (
          <Button
            variant="primary"
            size="sm"
            icon="plug"
            className="mt-3 w-full"
            onClick={() => {
              setOpen(false);
              openConnect();
            }}
          >
            Connect Fyers
          </Button>
        )}
      </Popover>
    </div>
  );
}
