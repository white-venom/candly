import { useCallback, useMemo, useState, type ReactNode } from "react";
import { SizingContext, readSizing, saveSizing, type Sizing } from "../lib/sizing";

export function SizingProvider({ children }: { children: ReactNode }) {
  const [sizing, setSizing] = useState<Sizing>(readSizing);
  const update = useCallback((next: Sizing) => {
    saveSizing(next);
    setSizing(next);
  }, []);
  const value = useMemo(() => ({ sizing, update }), [sizing, update]);
  return <SizingContext value={value}>{children}</SizingContext>;
}
