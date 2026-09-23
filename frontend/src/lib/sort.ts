export type SortDir = "asc" | "desc";

/** Sorts by one field; nulls always go last, whichever the direction. */
export function sortByKey<T, K extends keyof T>(rows: readonly T[], key: K, dir: SortDir): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[key];
    const y = b[key];
    if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string" && typeof y === "string") return sign * x.localeCompare(y);
    return sign * (Number(x) - Number(y));
  });
}
