import { useMemo, useState, type ReactNode } from "react";
import { useHotkeys } from "../../lib/shortcuts";
import { ShellContext, type ShellActions } from "../../lib/shell";
import { useTheme } from "../../lib/theme";
import { FyersConnectDialog } from "./FyersConnect";
import { Rail } from "./Rail";
import { SettingsDialog } from "./SettingsDialog";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { SyncToast } from "./SyncToast";

type Open = "connect" | "settings" | "shortcuts" | null;

/** Rail on the left, the page filling the rest of the window; app-wide dialogs live here. */
export function AppShell({ children }: { children?: ReactNode }) {
  const [open, setOpen] = useState<Open>(null);
  const { toggle } = useTheme();
  const actions = useMemo<ShellActions>(
    () => ({
      openConnect: () => setOpen("connect"),
      openSettings: () => setOpen("settings"),
      openShortcuts: () => setOpen("shortcuts"),
    }),
    [],
  );
  const close = () => setOpen(null);

  useHotkeys({ t: toggle, "?": actions.openShortcuts });

  return (
    <ShellContext value={actions}>
      <div className="flex h-svh overflow-hidden bg-page text-ink">
        <Rail />
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
      <FyersConnectDialog open={open === "connect"} onClose={close} />
      <SettingsDialog open={open === "settings"} onClose={close} />
      <ShortcutsDialog open={open === "shortcuts"} onClose={close} />
      <SyncToast />
    </ShellContext>
  );
}
