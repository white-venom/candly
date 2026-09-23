import { Fragment } from "react";
import { SHORTCUTS } from "../../lib/shortcuts";
import { Kbd } from "../ui/Controls";
import { Dialog } from "../ui/Dialog";

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Keyboard shortcuts">
      <dl className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-2.5 text-sm">
        {SHORTCUTS.map((s) => (
          <Fragment key={s.action}>
            <dt className="flex gap-1">
              {s.keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </dt>
            <dd className="text-ink-muted">{s.action}</dd>
          </Fragment>
        ))}
      </dl>
    </Dialog>
  );
}
