import { useEffect, useRef, useState } from "react";
import type { Candle, Forecast } from "../api/types";
import { useTheme } from "../lib/theme";
import type { DrawnLevel } from "./levels";
import { PriceChartController } from "./PriceChartController";
import type { Hover, IndicatorPlot, MarkerGlyph } from "./transforms";

type Props = {
  tf: string;
  /** instrument|tf: the view resets to the latest bars when it changes */
  viewKey: string;
  candles: Candle[];
  forming: Candle | null;
  markers: MarkerGlyph[];
  levels: DrawnLevel[];
  /** draw the levels dashed and dimmed: a newer session exists than the one they come from */
  levelsStale: boolean;
  forecast: Forecast | null;
  plots: IndicatorPlot[];
  label: string;
  onHover?: (hover: Hover | null) => void;
};

/** The canvas chart; it fills its parent and resizes with it. */
export function PriceChart({ tf, viewKey, candles, forming, markers, levels, levelsStale, forecast, plots, label, onHover }: Props) {
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
    controller?.setMarkers(markers);
  }, [controller, markers]);

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

  return <div ref={containerRef} role="img" aria-label={label} className="absolute inset-0" />;
}
