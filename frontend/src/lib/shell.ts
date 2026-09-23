import { createContext, useContext } from "react";

/** App-wide dialogs, opened from wherever they are needed (the rail, the paused strip, the trade card). */
export type ShellActions = {
  openConnect: () => void;
  openSettings: () => void;
  openShortcuts: () => void;
};

export const ShellContext = createContext<ShellActions>({
  openConnect: () => {},
  openSettings: () => {},
  openShortcuts: () => {},
});

export function useShell(): ShellActions {
  return useContext(ShellContext);
}
