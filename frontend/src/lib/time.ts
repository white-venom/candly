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
