import { useTheme } from "../../lib/theme";
import { IconButton } from "../ui/Button";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return <IconButton icon={theme === "dark" ? "sun" : "moon"} label={label} tip={`${label} (t)`} side="right" size="lg" onClick={toggle} />;
}
