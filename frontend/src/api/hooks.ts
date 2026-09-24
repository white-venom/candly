import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { withLastSteps } from "../lib/expected";
import { applySignalFilters, type SignalFilters } from "../lib/signalFilters";
import { apiGet, apiPost } from "./client";
import type {
  AccuracyResponse,
  CandlesResponse,
  Forecast,
  FyersCodeRequest,
  FyersStatus,
  Health,
  IndicatorInfo,
  IndicatorsResponse,
  Instrument,
  LedgerEntry,
  LevelsResponse,
  NewsItem,
  PatternSignal,
  ScannerRow,
  ScorecardResponse,
  SyncStatus,
} from "./types";

const SECOND = 1000;
const MINUTE = 60 * SECOND;

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiGet<Health>("/health", undefined, signal),
    // Poll faster while the Fyers first sync runs, so its progress moves.
    refetchInterval: (query) => (query.state.data?.sync?.status === "running" ? 5 * SECOND : 30 * SECOND),
  });
}

export function useStartSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<SyncStatus>("/sync/fyers", {}),
    onSettled: () => client.invalidateQueries({ queryKey: ["health"] }),
  });
}

export function useFyersStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["fyers-status"],
    queryFn: ({ signal }) => apiGet<FyersStatus>("/auth/fyers/status", undefined, signal),
    enabled,
    refetchInterval: 5 * MINUTE,
  });
}

export function useSubmitFyersCode() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: FyersCodeRequest) => apiPost<FyersStatus>("/auth/fyers/code", body),
    onSuccess: async () => {
      // The backend starts the first sync on a successful login; health carries its progress.
      await Promise.all([
        client.invalidateQueries({ queryKey: ["health"] }),
        client.invalidateQueries({ queryKey: ["fyers-status"] }),
      ]);
    },
  });
}

export function useInstruments() {
  return useQuery({
    queryKey: ["instruments"],
    queryFn: ({ signal }) => apiGet<Instrument[]>("/instruments", undefined, signal),
    staleTime: 5 * MINUTE,
  });
}

export function useCandles(instrument: string, tf: string, limit = 500) {
  return useQuery({
    queryKey: ["candles", instrument, tf, limit],
    queryFn: ({ signal }) => apiGet<CandlesResponse>("/candles", { instrument, tf, limit }, signal),
    enabled: Boolean(instrument && tf),
    refetchInterval: MINUTE,
  });
}

/** Releases one more of `count` items every `stepMs`, so their first requests are spread out. */
function useStaggered(count: number, stepMs: number): number {
  const [released, setReleased] = useState(1);
  useEffect(() => {
    if (released >= count) return;
    const timer = window.setTimeout(() => setReleased((r) => r + 1), stepMs);
    return () => window.clearTimeout(timer);
  }, [released, count, stepMs]);
  return released;
}

const QUOTE_STAGGER_MS = 1000;

/**
 * The last two daily bars of each instrument, with today's forming one, for the watchlist quote
 * (lib/quotes). While a market is open (`live`) its forming bar is rebuilt from today's 5m bars at the
 * broker, so the first requests start a second apart and repeat every minute only while it is open.
 */
export function useDailyQuotes(items: { id: string; live: boolean }[]): Map<string, CandlesResponse> {
  const released = useStaggered(items.length, QUOTE_STAGGER_MS);
  return useQueries({
    queries: items.map(({ id, live }, i) => ({
      queryKey: ["candles", id, "1D", 2],
      queryFn: ({ signal }: { signal: AbortSignal }) => apiGet<CandlesResponse>("/candles", { instrument: id, tf: "1D", limit: 2 }, signal),
      enabled: i < released,
      refetchInterval: live ? MINUTE : false,
      refetchOnWindowFocus: false,
      staleTime: live ? 30 * SECOND : 5 * MINUTE,
    })),
    combine: (results) => new Map(results.flatMap((r, i) => (r.data && items[i] ? [[items[i].id, r.data] as const] : []))),
  });
}

export function useIndicatorCatalog() {
  return useQuery({
    queryKey: ["indicator-catalog"],
    queryFn: ({ signal }) => apiGet<IndicatorInfo[]>("/indicators/catalog", undefined, signal),
    staleTime: Infinity,
  });
}

export function useIndicators(instrument: string, tf: string, names: string[], limit = 500) {
  const joined = [...names].sort().join(",");
  return useQuery({
    queryKey: ["indicators", instrument, tf, joined, limit],
    queryFn: ({ signal }) => apiGet<IndicatorsResponse>("/indicators", { instrument, tf, names: joined, limit }, signal),
    enabled: Boolean(instrument && tf && joined),
    refetchInterval: MINUTE,
  });
}

export function usePatterns(instrument: string, tf: string, filters: SignalFilters, limit = 200) {
  const { showNeutral, certifiedOnly } = filters;
  return useQuery({
    queryKey: ["patterns", instrument, tf, limit, showNeutral, certifiedOnly],
    queryFn: async ({ signal }) =>
      applySignalFilters(
        await apiGet<PatternSignal[]>(
          "/patterns",
          { instrument, tf, limit, directional_only: !showNeutral, certified_only: certifiedOnly },
          signal,
        ),
        { showNeutral, certifiedOnly },
      ),
    enabled: Boolean(instrument && tf),
    refetchInterval: MINUTE,
  });
}

export function useLevels(instrument: string, tf: string) {
  return useQuery({
    queryKey: ["levels", instrument, tf],
    queryFn: ({ signal }) => apiGet<LevelsResponse>("/levels", { instrument, tf }, signal),
    enabled: Boolean(instrument && tf),
    refetchInterval: MINUTE,
  });
}

/**
 * Polled every minute. The last forecast stays on screen while a refetch runs or fails (the query keeps
 * its data), and its steps carry over if a refetch comes back without any (lib/expected withLastSteps).
 */
export function useForecast(instrument: string, tf: string, steps = 3) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["forecast", instrument, tf, steps],
    queryFn: async ({ signal, queryKey }) =>
      withLastSteps(await apiGet<Forecast>("/forecast", { instrument, tf, steps }, signal), client.getQueryData<Forecast>(queryKey)),
    enabled: Boolean(instrument && tf),
    refetchInterval: MINUTE,
  });
}

export function useNews(instrument: string | null, limit = 50) {
  return useQuery({
    queryKey: ["news", instrument, limit],
    queryFn: ({ signal }) => apiGet<NewsItem[]>("/news", { instrument, limit }, signal),
    refetchInterval: 5 * MINUTE,
  });
}

export function useScanner(tf: string) {
  return useQuery({
    queryKey: ["scanner", tf],
    queryFn: ({ signal }) => apiGet<ScannerRow[]>("/scanner", { tf }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: MINUTE,
  });
}

export type ScorecardFilters = { tf: string; instrument: string | null; pattern: string | null; certifiedOnly: boolean };

export function useScorecard({ tf, instrument, pattern, certifiedOnly }: ScorecardFilters) {
  return useQuery({
    queryKey: ["scorecard", tf, instrument, pattern, certifiedOnly],
    queryFn: ({ signal }) =>
      apiGet<ScorecardResponse>("/scorecard", { tf, instrument, pattern, certified_only: certifiedOnly }, signal),
    placeholderData: keepPreviousData,
    staleTime: 5 * MINUTE,
  });
}

export type LedgerFilters = {
  instrument: string | null;
  tf: string | null;
  /** null lists every method. */
  method: string | null;
  status?: LedgerEntry["status"] | null;
  limit?: number;
};

export function useLedger({ instrument, tf, method, status = null, limit = 100 }: LedgerFilters) {
  return useQuery({
    queryKey: ["ledger", instrument, tf, method, status, limit],
    queryFn: ({ signal }) => apiGet<LedgerEntry[]>("/ledger", { instrument, tf, method, status, limit }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: MINUTE,
  });
}

/** Accuracy is only meaningful for one method at a time; a null method skips the request. */
export function useAccuracy(instrument: string | null, tf: string | null, days: number, method: string | null) {
  return useQuery({
    queryKey: ["accuracy", instrument, tf, days, method],
    queryFn: ({ signal }) => apiGet<AccuracyResponse>("/accuracy", { instrument, tf, days, method }, signal),
    enabled: method !== null,
    placeholderData: keepPreviousData,
    refetchInterval: 5 * MINUTE,
  });
}
