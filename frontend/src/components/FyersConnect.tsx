import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useSubmitFyersCode } from "../api/hooks";
import type { Health } from "../api/types";
import { describeError } from "../lib/errors";
import { formatDateTimeIST } from "../lib/time";

function submitError(error: unknown): string {
  if (error instanceof ApiError && error.status === 400) return error.detail;
  return describeError(error).title;
}

/** Paste-code login: Fyers only redirects to its own page, so the user copies the URL back here. */
export function FyersConnect({ health }: { health: Health | undefined }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const submit = useSubmitFyersCode();
  const panelId = useId();
  const inputId = useId();
  const firstLink = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    if (open) firstLink.current?.focus();
  }, [open]);

  const needed = health?.keys.fyers === true && !health.keys.fyers_connected;
  const justConnected = submit.isSuccess && submit.data.connected;
  if (!needed && !(open && justConnected)) return null;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const value = code.trim();
    if (value) submit.mutate({ code: value });
  };

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-forming px-3 py-1.5 text-xs font-medium text-forming hover:bg-raised"
      >
        Connect Fyers
      </button>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Connect Fyers"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
          className="absolute right-0 top-full z-30 mt-2 w-[min(26rem,calc(100vw-2rem))] rounded-lg border border-line-strong bg-surface p-4 text-sm shadow-lg"
        >
          <ol className="flex list-decimal flex-col gap-3 pl-5 text-ink">
            <li>
              <a ref={firstLink} href="/api/auth/fyers/login" target="_blank" rel="noopener noreferrer" className="font-medium">
                Log in to Fyers
              </a>{" "}
              <span className="text-ink-muted">(opens in a new tab)</span>
            </li>
            <li>
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
                  className="w-full rounded-md border border-line-strong bg-page px-2 py-1.5 font-mono text-xs text-ink placeholder:text-ink-faint"
                />
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={submit.isPending || !code.trim()}
                    className="rounded-md border border-accent px-3 py-1.5 text-xs font-medium text-accent hover:bg-raised disabled:opacity-50"
                  >
                    {submit.isPending ? "Submitting…" : "Submit"}
                  </button>
                  <button type="button" onClick={() => setOpen(false)} className="px-2 py-1.5 text-xs text-ink-muted hover:text-ink">
                    Close
                  </button>
                </div>
              </form>
            </li>
          </ol>
          {submit.isError && (
            <p role="alert" className="mt-3 text-danger">
              {submitError(submit.error)}
            </p>
          )}
          {submit.isSuccess && (
            <p role="status" className={submit.data.connected ? "mt-3 text-up" : "mt-3 text-danger"}>
              {submit.data.connected
                ? submit.data.expires_at
                  ? `Connected until ${formatDateTimeIST(submit.data.expires_at)} IST`
                  : "Connected"
                : "Fyers did not confirm the connection — try logging in again"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
