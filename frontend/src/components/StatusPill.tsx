import type { UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useFyersStatus } from "../api/hooks";
import type { Health } from "../api/types";
import { describeError } from "../lib/errors";
import { humanize } from "../lib/format";
import { formatTimeIST } from "../lib/time";

function Pill({ children, tone = "normal", label }: { children: ReactNode; tone?: "normal" | "danger"; label: string }) {
  return (
    <div
      role="status"
      aria-label={label}
      className={clsx(
        "flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-full border px-3 py-1 text-xs",
        tone === "danger" ? "border-danger text-danger" : "border-line-strong text-ink-muted",
      )}
    >
      {children}
    </div>
  );
}

function Dot({ on }: { on: boolean }) {
  return <span aria-hidden="true" className={clsx("inline-block size-2 rounded-full", on ? "bg-up" : "bg-ink-faint")} />;
}

export function StatusPill({ health }: { health: UseQueryResult<Health> }) {
  const fyersEnabled = health.data?.keys.fyers === true && health.data.keys.fyers_connected;
  const fyers = useFyersStatus(Boolean(fyersEnabled));

  if (health.data === undefined) {
    if (health.isError) {
      const info = describeError(health.error);
      return (
        <Pill tone="danger" label="API status">
          <span title={info.title}>{info.kind === "unreachable" ? "Backend offline" : "API error"}</span>
        </Pill>
      );
    }
    return <Pill label="API status">Checking API…</Pill>;
  }

  const h = health.data;
  const expires = fyers.data?.connected && fyers.data.expires_at ? ` until ${formatTimeIST(fyers.data.expires_at)} IST` : "";
  const fyersText = !h.keys.fyers ? "no key" : h.keys.fyers_connected ? `connected${expires}` : "not connected";

  return (
    <Pill label="System status">
      <span>
        Data: <span className="text-ink">{h.data_source}</span>
      </span>
      {h.markets.map((m) => (
        <span key={m.exchange} className="inline-flex items-center gap-1" title={`${m.exchange} session phase: ${humanize(m.phase)}`}>
          <Dot on={m.open} />
          <span className="text-ink">{m.exchange}</span>
          {m.open ? "open" : "closed"}
          {m.phase && m.phase !== (m.open ? "open" : "closed") && <span className="text-ink-faint">· {humanize(m.phase)}</span>}
        </span>
      ))}
      <span>
        Fyers: <span className={h.keys.fyers_connected ? "text-ink" : undefined}>{fyersText}</span>
      </span>
      {health.isError && <span className="text-danger">stale</span>}
    </Pill>
  );
}
