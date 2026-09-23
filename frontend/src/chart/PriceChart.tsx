import { useEffect, useRef, useState } from "react";
import type { Candle, Forecast, Level, PatternSignal } from "../api/types";
import { useTheme } from "../lib/theme";
import { PriceChartController } from "./PriceChartController";
import type { HoverBar, IndicatorPlot } from "./transforms";

type Props = {
  tf: string;
  /** instrument|tf: the view resets to the latest bars when it changes */
  viewKey: string;
  candles: Candle[];
  forming: Candle | null;
  signals: PatternSignal[];
  levels: Level[];
  /** draw the levels dashed and dimmed: a newer session exists than the one they come from */
  levelsStale: boolean;
  forecast: Forecast | null;
  plots: IndicatorPlot[];
  height: number;
  label: string;
  onHover?: (bar: HoverBar | null) => void;
};

export function PriceChart({ tf, viewKey, candles, forming, signals, levels, levelsStale, forecast, plots, height, label, onHover }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [controller, setController] = useState<PriceChartController | null>(null);
  const { theme } = useTheme();
  const atMount = useRef({ theme, tf });
  const viewedKey = useRef<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const c = new PriceChartController(containerRef.current, atMount.current.theme, atMount.current.tf);
    setController(c);
    return () => {
      viewedKey.current = null;
      c.destroy();
      setController(null);
    };
  }, []);

  useEffect(() => {
    controller?.setTheme(theme);
  }, [controller, theme]);

  useEffect(() => {
    controller?.setTimeframe(tf);
  }, [controller, tf]);

  useEffect(() => {
    if (!controller) return;
    controller.setCandles(candles, forming);
    if (candles.length > 0 && viewedKey.current !== viewKey) {
      controller.resetView();
      viewedKey.current = viewKey;
    }
  }, [controller, candles, forming, viewKey]);

  useEffect(() => {
    controller?.setSignals(signals);
  }, [controller, signals]);

  useEffect(() => {
    controller?.setLevels(levels, levelsStale);
  }, [controller, levels, levelsStale]);

  useEffect(() => {
    controller?.setForecast(forecast);
  }, [controller, forecast]);

  useEffect(() => {
    controller?.setIndicators(plots);
  }, [controller, plots]);

  useEffect(() => {
    if (!controller || !onHover) return;
    return controller.onHover(onHover);
  }, [controller, onHover]);

  return <div ref={containerRef} role="img" aria-label={label} className="w-full" style={{ height }} />;
}
