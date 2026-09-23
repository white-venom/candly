import { TickMarkType, type Time, type TickMarkFormatter } from "lightweight-charts";

export const IST = "Asia/Kolkata";

// en-US parts give stable month names ("Sep", not "Sept") across ICU versions.
const partsFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: IST,
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

type IstParts = { year: string; month: string; day: string; hour: string; minute: string; second: string };

function istParts(unix: number): IstParts {
  const out: Record<string, string> = {};
  for (const part of partsFormat.formatToParts(new Date(unix * 1000))) {
    if (part.type !== "literal") out[part.type] = part.value;
  }
  return out as IstParts;
}

export function formatDateIST(unix: number): string {
  const p = istParts(unix);
  return `${p.day} ${p.month} ${p.year}`;
}

export function formatTimeIST(unix: number): string {
  const p = istParts(unix);
  return `${p.hour}:${p.minute}`;
}

export function formatDateTimeIST(unix: number): string {
  const p = istParts(unix);
  return `${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}`;
}

/** A daily bar's time is its session open, so only the date means anything. */
export function formatBarTimeIST(unix: number, tf: string): string {
  return tf === "1D" ? formatDateIST(unix) : formatDateTimeIST(unix);
}

export function timeToUnix(time: Time): number {
  if (typeof time === "number") return time;
  if (typeof time === "string") return Date.parse(`${time}T00:00:00Z`) / 1000;
  return Date.UTC(time.year, time.month - 1, time.day) / 1000;
}

/** For `localization.timeFormatter`: the crosshair label on the time axis. */
export function chartTimeFormatter(tf: string): (time: Time) => string {
  return (time) => formatBarTimeIST(timeToUnix(time), tf);
}

/** For `timeScale.tickMarkFormatter`. */
export const istTickMarkFormatter: TickMarkFormatter = (time, tickMarkType) => {
  const p = istParts(timeToUnix(time));
  switch (tickMarkType) {
    case TickMarkType.Year:
      return p.year;
    case TickMarkType.Month:
      return p.month;
    case TickMarkType.DayOfMonth:
      return `${p.day} ${p.month}`;
    case TickMarkType.Time:
      return `${p.hour}:${p.minute}`;
    case TickMarkType.TimeWithSeconds:
      return `${p.hour}:${p.minute}:${p.second}`;
    default:
      return null;
  }
};

export function nowUnix(): number {
  return Math.floor(Date.now() / 1000);
}
