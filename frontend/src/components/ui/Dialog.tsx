import clsx from "clsx";
import { useEffect, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { IconButton } from "./Button";
import { focusFirst, trapTab } from "./focus";

type Props = {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  className?: string;
  /** a side sheet from the right instead of a centred box */
  drawer?: boolean;
};

/** Modal: Escape or a click on the backdrop closes it; Tab stays inside; focus returns to the opener. */
export function Dialog({ open, onClose, title, children, className, drawer = false }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  useLayoutEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    focusFirst(panel.current);
    return () => opener?.focus();
  }, [open]);

  if (!open) return null;
  return (
    <div
      className={clsx("fixed inset-0 z-50 flex bg-page/70", drawer ? "justify-end" : "items-start justify-center overflow-y-auto p-4 pt-[12vh]")}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close.current();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            close.current();
          } else trapTab(e, panel.current);
        }}
        className={clsx(
          "flex flex-col border-line bg-surface shadow-lg shadow-page/40",
          drawer ? "h-full w-[min(26rem,100vw)] border-l" : "w-full max-w-md rounded-lg border",
          className,
        )}
      >
        <div className={clsx("flex shrink-0 items-center gap-3", drawer ? "h-12 border-b border-line px-4" : "px-5 pt-4")}>
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink">
            {title}
          </h2>
          <IconButton icon="close" label="Close" tip={null} data-close onClick={() => close.current()} className="-mr-2" />
        </div>
        <div className={clsx(drawer ? "min-h-0 flex-1 overflow-y-auto" : "px-5 pt-3 pb-5")}>{children}</div>
      </div>
    </div>
  );
}
