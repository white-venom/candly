import clsx from "clsx";
import type { ReactNode } from "react";
import { NoticeStrip } from "./NoticeStrip";

/** Layout for the table pages: a 48px header, notices, an optional filter bar, then scrolling content. */
export function Page({
  title,
  description,
  actions,
  toolbar,
  children,
  wide = false,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <>
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-surface px-4">
        <div className="flex min-w-0 items-baseline gap-3">
          <h1 className="shrink-0 text-[15px] font-semibold text-ink">{title}</h1>
          {description && <p className="truncate text-xs text-ink-faint">{description}</p>}
        </div>
        {actions && <div className="ml-auto flex shrink-0 items-center gap-3">{actions}</div>}
      </header>
      <NoticeStrip />
      {toolbar && (
        <div role="group" aria-label="Filters" className="flex min-h-11 shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-1.5">
          {toolbar}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={clsx("mx-auto flex flex-col gap-4 p-4", wide ? "max-w-[1760px]" : "max-w-[1440px]")}>{children}</div>
      </div>
    </>
  );
}
