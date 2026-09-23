import { useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { ThemeContext, applyTheme, initialTheme, storeTheme, type Theme } from "../lib/theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Only an explicit toggle is saved; until then the system preference keeps deciding.
  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    storeTheme(next);
    setTheme(next);
  }, [theme]);

  const value = useMemo(() => ({ theme, toggle }), [theme, toggle]);
  return <ThemeContext value={value}>{children}</ThemeContext>;
}
