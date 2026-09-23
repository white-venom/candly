import { useCallback, useState } from "react";
import { readStorage, writeStorage } from "./storage";

/** Reads a saved JSON value, keeping only the fields of `fallback` that come back with the same type. */
function readPref<T extends Record<string, unknown>>(key: string, fallback: T): T {
  const raw = readStorage(key);
  if (!raw) return fallback;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return fallback;
    const out = { ...fallback };
    for (const k of Object.keys(fallback) as (keyof T)[]) {
      const v = (value as Record<string, unknown>)[k as string];
      if (typeof v === typeof fallback[k]) out[k] = v as T[keyof T];
    }
    return out;
  } catch {
    return fallback;
  }
}

/** A small settings object remembered in this browser. */
export function usePref<T extends Record<string, unknown>>(key: string, fallback: T): [T, (patch: Partial<T>) => void] {
  const [value, setValue] = useState<T>(() => readPref(key, fallback));
  const update = useCallback(
    (patch: Partial<T>) =>
      setValue((prev) => {
        const next = { ...prev, ...patch };
        writeStorage(key, JSON.stringify(next));
        return next;
      }),
    [key],
  );
  return [value, update];
}
