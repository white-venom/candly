import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

export function useForecast(instrument: string, tf: string, steps = 3) {
  return useQuery({
    queryKey: ["forecast", instrument, tf, steps],
    queryFn: ({ signal }) => apiGet<Forecast>("/forecast", { instrument, tf, steps }, signal),
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
