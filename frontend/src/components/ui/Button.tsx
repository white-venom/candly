import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { Tip, type TipSide } from "./Tooltip";

type Variant = "primary" | "secondary" | "ghost";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-page hover:opacity-90",
  secondary: "border border-line bg-surface text-ink hover:border-line-strong hover:bg-raised",
  ghost: "text-ink-muted hover:bg-raised hover:text-ink",
};

const SIZE = { sm: "h-7 gap-1.5 px-2.5 text-xs", md: "h-8 gap-2 px-3 text-sm" };

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: keyof typeof SIZE; icon?: IconName };

export function Button({ variant = "secondary", size = "md", icon, children, className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(
        "inline-flex shrink-0 items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...props}
    >
      {icon && <Icon name={icon} className={size === "sm" ? "size-3.5" : "size-4"} />}
      {children}
    </button>
  );
}

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: IconName;
  label: string;
  /** tooltip text; defaults to the label. `null` for none. */
  tip?: ReactNode | null;
  side?: TipSide;
  pressed?: boolean;
  size?: "sm" | "md" | "lg";
};

const ICON_SIZE = { sm: "size-7", md: "size-8", lg: "size-10" };

export function IconButton({ icon, label, tip, side = "bottom", pressed, size = "md", className, ...props }: IconButtonProps) {
  const button = (
    <button
      type="button"
      aria-label={label}
      aria-pressed={pressed}
      className={clsx(
        "inline-flex shrink-0 items-center justify-center rounded-md transition-colors",
        ICON_SIZE[size],
        pressed ? "text-accent hover:bg-raised" : pressed === false ? "text-ink-faint hover:bg-raised hover:text-ink" : "text-ink-muted hover:bg-raised hover:text-ink",
        className,
      )}
      {...props}
    >
      <Icon name={icon} className={size === "lg" ? "size-5" : "size-[18px]"} />
    </button>
  );
  return tip === null ? button : <Tip label={tip ?? label} side={side}>{button}</Tip>;
}
