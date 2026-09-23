import { TickMarkType, type Time, type TickMarkFormatter } from "lightweight-charts";
import { formatBarTimeIST, istParts } from "../lib/time";

// Kept out of lib/time so pages without a chart don't pull lightweight-charts into their bundle.

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
