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

export function istParts(unix: number): IstParts {
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

export function nowUnix(): number {
  return Math.floor(Date.now() / 1000);
}

// ---- Human formats: "10:15" today, "23 Sep, 10:15" otherwise; never ISO. ----

/** "23 Sep", with the year only when it isn't the current one. */
export function formatDayIST(unix: number, now = nowUnix()): string {
  const p = istParts(unix);
  const day = `${Number(p.day)} ${p.month}`;
  return p.year === istParts(now).year ? day : `${day} ${p.year}`;
}

const weekdayFormat = new Intl.DateTimeFormat("en-US", { timeZone: IST, weekday: "short" });

/** "Thu 25 Sep": a session named by its IST day. */
export function formatSessionIST(unix: number): string {
  const p = istParts(unix);
  return `${weekdayFormat.format(new Date(unix * 1000))} ${Number(p.day)} ${p.month}`;
}

export function sameDayIST(a: number, b: number): boolean {
  const [x, y] = [istParts(a), istParts(b)];
  return x.year === y.year && x.month === y.month && x.day === y.day;
}

/** "10:15" on the same IST day as `now`, else "23 Sep, 10:15". */
export function formatWhenIST(unix: number, now = nowUnix()): string {
  const p = istParts(unix);
  const time = `${p.hour}:${p.minute}`;
  if (sameDayIST(unix, now)) return time;
  return `${formatDayIST(unix, now)}, ${time}`;
}

/** Daily bars show the day only; intraday bars the time (and the day when it isn't today). */
export function formatBarWhenIST(unix: number, tf: string, now = nowUnix()): string {
  return tf === "1D" ? formatDayIST(unix, now) : formatWhenIST(unix, now);
}

/** "just now", "5m ago", "2h ago", "3d ago", then the day. */
export function relativeTime(unix: number, now = nowUnix()): string {
  const s = now - unix;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  return formatDayIST(unix, now);
}

/** ISO 8601 with an offset (as the backend writes it) → UNIX seconds, or null. */
export function isoToUnix(iso: string): number | null {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A plain calendar date "2025-10-01" → "1 Oct 2025"; anything else is returned as sent. */
export function formatIsoDate(date: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return date;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${Number(m[3])} ${month} ${m[1]}` : date;
}
