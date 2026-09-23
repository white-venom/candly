import { useId, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import { useSubmitFyersCode } from "../../api/hooks";
import { friendlyError, humanizeText } from "../../lib/messages";
import { formatWhenIST } from "../../lib/time";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

function submitError(error: unknown): string {
  if (error instanceof ApiError && error.status === 400) return humanizeText(error.detail);
  return friendlyError(error).title;
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="flex size-6 shrink-0 items-center justify-center rounded-full bg-raised text-xs font-semibold text-ink-muted">
        {n}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">{children}</div>
    </li>
  );
}

/** Paste-code login: Fyers only redirects to its own page, so the user copies that URL back here. */
function PasteCodePanel({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState("");
  const submit = useSubmitFyersCode();
  const inputId = useId();
  const connected = submit.isSuccess && submit.data.connected;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const value = code.trim();
    if (value) submit.mutate({ code: value });
  };

  return (
    <div className="flex flex-col gap-4 text-sm">
      <ol className="flex flex-col gap-4">
        <Step n={1}>
          <a href="/api/auth/fyers/login" target="_blank" rel="noopener noreferrer" className="font-medium">
            Log in to Fyers
          </a>
          <span className="text-ink-faint"> · opens in a new tab</span>
        </Step>
        <Step n={2}>
          <form onSubmit={onSubmit} className="flex flex-col gap-2">
            <label htmlFor={inputId} className="text-ink-muted">
              After logging in, copy the full address-bar URL from the Fyers page and paste it here
            </label>
            <input
              id={inputId}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="https://trade.fyers.in/api-login/redirect-uri/index.html?auth_code=…"
              autoComplete="off"
              spellCheck={false}
              className="h-8 w-full rounded-md border border-line bg-page px-2 font-mono text-xs text-ink placeholder:text-ink-faint hover:border-line-strong"
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>
                {connected ? "Done" : "Cancel"}
              </Button>
              <Button type="submit" variant="primary" size="sm" disabled={submit.isPending || !code.trim()}>
                {submit.isPending ? "Submitting…" : "Submit"}
              </Button>
            </div>
          </form>
        </Step>
      </ol>
      {submit.isError && (
        <p role="alert" className="rounded-md border border-danger/40 px-3 py-2 text-danger">
          {submitError(submit.error)}
        </p>
      )}
      {submit.isSuccess &&
        (connected ? (
          <div role="status" className="rounded-md border border-up/40 px-3 py-2">
            <p className="font-medium text-up">Connected — setting everything up now</p>
            <p className="mt-0.5 text-xs text-ink-muted">
              Progress shows under the top bar.
              {submit.data.expires_at && ` The login lasts until ${formatWhenIST(submit.data.expires_at)} IST.`}
            </p>
          </div>
        ) : (
          <p role="status" className="rounded-md border border-danger/40 px-3 py-2 text-danger">
            Fyers did not confirm the connection — try logging in again
          </p>
        ))}
    </div>
  );
}

export function FyersConnectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Connect Fyers">
      <p className="mb-4 text-xs text-ink-muted">Live candles and the backfill come from your Fyers account. The login lasts until the next morning.</p>
      <PasteCodePanel onClose={onClose} />
    </Dialog>
  );
}
