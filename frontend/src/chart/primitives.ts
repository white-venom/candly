import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, Logical, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { Direction } from "../api/types";
import type { ExpectedRange } from "../lib/expected";
import type { ExpectedLook, TagLook } from "./chartTheme";
import { expectedBoxGeometry, type BoxGeometry } from "./expectedBox";
import { placeTags } from "./tagLayout";
import type { MarkerGlyph } from "./transforms";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];
type Attached = SeriesAttachedParameter<Time>;

/** Shared plumbing: remembers the chart and series, and asks for a redraw when the data changes. */
abstract class Primitive implements ISeriesPrimitive<Time> {
  protected param: Attached | null = null;
  private readonly views: IPrimitivePaneView[];

  constructor(zOrder: "bottom" | "normal" | "top") {
    this.views = [{ zOrder: () => zOrder, renderer: () => ({ draw: (target: Target) => this.draw(target) }) }];
  }

  attached(param: Attached): void {
    this.param = param;
  }

  detached(): void {
    this.param = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  protected redraw(): void {
    this.param?.requestUpdate();
  }

  protected abstract draw(target: Target): void;
}

type BarExtent = { high: number; low: number };

/**
 * Pattern arrows without text: a small triangle under the low (bullish) or over the high (bearish),
 * a diamond for neutral. Confirmed patterns are filled, forming ones hollow.
 */
export class PatternMarkers extends Primitive {
  private glyphs: MarkerGlyph[] = [];
  private bars = new Map<number, BarExtent>();
  private colors: Record<Direction, string> = { bullish: "#000", bearish: "#000", neutral: "#000" };
  private background = "#000";

  constructor() {
    super("top");
  }

  set(glyphs: MarkerGlyph[], bars: Map<number, BarExtent>): void {
    this.glyphs = glyphs;
    this.bars = bars;
    this.redraw();
  }

  setColors(colors: Record<Direction, string>, background: string): void {
    this.colors = colors;
    this.background = background;
    this.redraw();
  }

  protected draw(target: Target): void {
    const p = this.param;
    if (!p || this.glyphs.length === 0) return;
    const ts = p.chart.timeScale();
    const a = ts.logicalToCoordinate(0 as Logical);
    const b = ts.logicalToCoordinate(1 as Logical);
    const spacing = a !== null && b !== null ? Math.abs(b - a) : 8;
    const w = Math.max(5, Math.min(9, spacing * 0.75));
    const h = w * 0.8;
    const gap = 4;

    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.lineWidth = 1.25;
      ctx.lineJoin = "round";
      for (const g of this.glyphs) {
        const x = ts.timeToCoordinate(g.time as Time);
        const bar = this.bars.get(g.time);
        if (x === null || !bar) continue;
        const below = g.direction === "bullish";
        const edge = p.series.priceToCoordinate(below ? bar.low : bar.high);
        if (edge === null) continue;
        const offset = gap + g.stack * (h + 3);
        const color = this.colors[g.direction];
        ctx.beginPath();
        if (g.direction === "neutral") {
          const cy = edge - offset - h / 2;
          ctx.moveTo(x, cy - h / 2);
          ctx.lineTo(x + h / 2, cy);
          ctx.lineTo(x, cy + h / 2);
          ctx.lineTo(x - h / 2, cy);
        } else if (below) {
          const top = edge + offset;
          ctx.moveTo(x, top);
          ctx.lineTo(x + w / 2, top + h);
          ctx.lineTo(x - w / 2, top + h);
        } else {
          const bottom = edge - offset;
          ctx.moveTo(x, bottom);
          ctx.lineTo(x + w / 2, bottom - h);
          ctx.lineTo(x - w / 2, bottom - h);
        }
        ctx.closePath();
        if (g.filled) {
          ctx.fillStyle = color;
          ctx.fill();
        } else {
          ctx.fillStyle = this.background;
          ctx.fill();
          ctx.strokeStyle = color;
          ctx.stroke();
        }
      }
    });
  }
}

/** A line across the pane (optional) and a full-width tag on the price axis. */
export type PriceTag = {
  price: number;
  /** "PDH", "PDH·R1", "Stop", or the last price */
  label: string;
  look: TagLook;
  /** draw the line in the pane (the last price already has the series' own line) */
  line: boolean;
  /** never moved to make room: the last price and the stop */
  fixed?: boolean;
};

const TAG_H = 17;
const TAG_GAP_FILL = 12;
const TAG_FONT = '500 11px Inter, "Segoe UI", system-ui, sans-serif';

/**
 * Levels, the stop and the last price as short tags that fill the price-axis width, spread so none
 * overlap (a level with no room keeps its line but loses its tag; earlier tags are more important).
 * Nothing is written inside the pane, so the forecast area at its right edge stays clear.
 */
export class PriceTags extends Primitive {
  private tags: PriceTag[] = [];
  private readonly axisViews: IPrimitivePaneView[];

  constructor() {
    super("normal");
    this.axisViews = [{ zOrder: () => "top", renderer: () => ({ draw: (target: Target) => this.drawAxis(target) }) }];
  }

  set(tags: PriceTag[]): void {
    this.tags = tags;
    this.redraw();
  }

  priceAxisPaneViews(): readonly IPrimitivePaneView[] {
    return this.axisViews;
  }

  protected draw(target: Target): void {
    const p = this.param;
    if (!p) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      for (const tag of this.tags) {
        const y = tag.line ? p.series.priceToCoordinate(tag.price) : null;
        if (y === null) continue;
        const crisp = Math.round(y) + 0.5;
        ctx.beginPath();
        ctx.setLineDash(tag.look.dash);
        ctx.lineWidth = tag.look.width;
        ctx.strokeStyle = tag.look.color;
        ctx.moveTo(0, crisp);
        ctx.lineTo(mediaSize.width, crisp);
        ctx.stroke();
      }
      ctx.setLineDash([]);
    });
  }

  private drawAxis(target: Target): void {
    const p = this.param;
    if (!p) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      // Prices scrolled out of view get no tag, rather than one pulled in at the edge.
      const shown = this.tags.flatMap((tag) => {
        const y = p.series.priceToCoordinate(tag.price);
        return y === null || y < 0 || y > mediaSize.height ? [] : [{ tag, y }];
      });
      if (shown.length === 0) return;
      const ys = placeTags(
        shown.map((s) => ({ y: s.y, fixed: s.tag.fixed === true })),
        TAG_H,
        mediaSize.height,
      );
      ctx.font = TAG_FONT;
      ctx.textBaseline = "middle";
      const placed = shown
        .flatMap(({ tag }, i) => (ys[i] === null ? [] : [{ tag, y: Math.round(ys[i]) }]))
        .sort((a, b) => a.y - b.y);
      placed.forEach(({ tag, y }, i) => {
        // Close a sliver of gap to the next tag, so no half-hidden axis number peeks through.
        const gap = i + 1 < placed.length ? placed[i + 1].y - y - TAG_H : Infinity;
        const height = gap > 0 && gap < TAG_GAP_FILL ? TAG_H + gap : TAG_H;
        ctx.fillStyle = tag.look.tagBackground;
        ctx.fillRect(0, y - TAG_H / 2, mediaSize.width, height);
        ctx.fillStyle = tag.look.tagText;
        ctx.fillText(fitLabel(ctx, tag.label, mediaSize.width - 10), 6, y + 0.5);
      });
    });
  }
}

/** "PDC·Pivot·TC" → "PDC +2" when the full label doesn't fit the axis. */
function fitLabel(ctx: CanvasRenderingContext2D, label: string, width: number): string {
  if (ctx.measureText(label).width <= width) return label;
  const parts = label.split("·");
  return parts.length > 1 ? `${parts[0]} +${parts.length - 1}` : label;
}

/** The translucent p10–p90 cone behind the forecast. */
export class BandFill extends Primitive {
  private points: { time: number; low: number; high: number }[] = [];
  private color = "transparent";

  constructor() {
    super("bottom");
  }

  set(points: { time: number; low: number; high: number }[], color: string): void {
    this.points = points;
    this.color = color;
    this.redraw();
  }

  protected draw(target: Target): void {
    const p = this.param;
    if (!p || this.points.length < 2) return;
    const ts = p.chart.timeScale();
    const upper: [number, number][] = [];
    const lower: [number, number][] = [];
    for (const pt of this.points) {
      const x = ts.timeToCoordinate(pt.time as Time);
      const hi = p.series.priceToCoordinate(pt.high);
      const lo = p.series.priceToCoordinate(pt.low);
      if (x === null || hi === null || lo === null) continue;
      upper.push([x, hi]);
      lower.push([x, lo]);
    }
    if (upper.length < 2) return;
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.beginPath();
      upper.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      for (let i = lower.length - 1; i >= 0; i--) ctx.lineTo(lower[i][0], lower[i][1]);
      ctx.closePath();
      ctx.fillStyle = this.color;
      ctx.fill();
    });
  }
}

const EXPECTED_FONT = '600 10px Inter, "Segoe UI", system-ui, sans-serif';

/**
 * The next bar's 80% close range as an outlined box around its slot, with a median tick, drawn under
 * the candles so the live bar stays on top. The "Expected" label is drawn above everything.
 */
export class ExpectedBox extends Primitive {
  private range: ExpectedRange | null = null;
  private barHigh: number | null = null;
  private look: ExpectedLook = { stroke: "transparent", fill: "transparent", text: "transparent", halo: "transparent" };
  private readonly allViews: readonly IPrimitivePaneView[];

  constructor() {
    super("bottom");
    this.allViews = [...super.paneViews(), { zOrder: () => "top", renderer: () => ({ draw: (target: Target) => this.drawLabel(target) }) }];
  }

  /** `barHigh`: the high of the candle already in the slot (the forming bar), if any. */
  set(range: ExpectedRange | null, barHigh: number | null, look: ExpectedLook): void {
    this.range = range;
    this.barHigh = barHigh;
    this.look = look;
    this.redraw();
  }

  override paneViews(): readonly IPrimitivePaneView[] {
    return this.allViews;
  }

  private geometry(): BoxGeometry | null {
    const p = this.param;
    const r = this.range;
    if (!p || !r) return null;
    const ts = p.chart.timeScale();
    const a = ts.logicalToCoordinate(0 as Logical);
    const b = ts.logicalToCoordinate(1 as Logical);
    const spacing = a !== null && b !== null ? Math.abs(b - a) : 6;
    const y = (price: number | null) => (price === null ? null : p.series.priceToCoordinate(price));
    return expectedBoxGeometry(ts.timeToCoordinate(r.time as Time), spacing, { high: y(r.high), mid: y(r.mid), low: y(r.low) }, y(this.barHigh));
  }

  protected draw(target: Target): void {
    const g = this.geometry();
    if (!g) return;
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.fillStyle = this.look.fill;
      ctx.fillRect(g.left, g.top, g.width, g.height);
      ctx.strokeStyle = this.look.stroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(Math.round(g.left) + 0.5, Math.round(g.top) + 0.5, Math.max(1, Math.round(g.width) - 1), Math.max(1, Math.round(g.height) - 1));
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(g.tick.x1, g.tick.y);
      ctx.lineTo(g.tick.x2, g.tick.y);
      ctx.stroke();
    });
  }

  private drawLabel(target: Target): void {
    const g = this.geometry();
    if (!g) return;
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.font = EXPECTED_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = g.label.baseline;
      ctx.lineJoin = "round";
      ctx.lineWidth = 3;
      ctx.strokeStyle = this.look.halo;
      ctx.strokeText("Expected", g.label.x, g.label.y);
      ctx.fillStyle = this.look.text;
      ctx.fillText("Expected", g.label.x, g.label.y);
    });
  }
}
