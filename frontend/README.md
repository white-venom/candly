# candly dashboard

React + TypeScript front end for candly: the chart with patterns, indicators, levels and the forecast, plus Scanner, Scorecard, Accuracy (expected vs actual) and News pages. The backend computes every number; this app only displays them.

## Commands (run inside `frontend/`)

| | |
|---|---|
| `npm run dev` | http://localhost:5173, proxies `/api` to `http://127.0.0.1:8000` |
| `npm run build` | type-check (`tsc -b`) and production build |
| `npm run lint` | oxlint |
| `npm run test` | vitest (jsdom) |

Start the API first (see the repo's CLAUDE.md). Without it every view shows "Backend not running".

## Layout

```
src/api/                 types.ts mirrors docs/CONTRACTS.md §4; client.ts (typed errors); hooks.ts (TanStack Query)
src/chart/               lightweight-charts v5: chartTheme.ts (theme → options), transforms.ts (data → series),
                         levels.ts (key levels, merging), primitives.ts (pattern arrows, band cone, axis tags),
                         PriceChartController.ts (creates the chart once, applies data/theme onto it), PriceChart.tsx
src/components/shell/    rail, notice strip (paused / sync / expiry), Fyers connect, settings, shortcuts, page frame
src/components/workspace/ chart page: watchlist, top bar, indicators menu, layer menus, chart area, legend
src/components/setup/    right panel: verdict, trade plan, why, recent signals, news
src/components/ui/, viz/ primitives (buttons, popover, dialog, states, table) and SVG charts
src/pages/               Chart, Scanner, Scorecard, Accuracy, News
src/lib/                 messages.ts (every backend reason/error in plain words), IST time, tokens, theme, prefs
```

Routes: `/chart/NSE:RELIANCE/1D`, `/scanner?tf=`, `/scorecard?tf=&instrument=&pattern=&certified=1`, `/accuracy?instrument=&tf=&days=&method=`, `/news?instrument=&sentiment=&event=`.

Keys: `/` search, `1`–`4` timeframe, `[` `]` previous/next instrument, `↑` `↓` watchlist, `i` indicators, `t` theme, `?` help.

## Time

The API sends UTC UNIX seconds. They go into the chart unchanged; IST appears only through `localization.timeFormatter`, `timeScale.tickMarkFormatter` and the `src/lib/time.ts` formatters.

## Theme

- Colour tokens live in `src/index.css` under `:root[data-theme="dark"]` and `:root[data-theme="light"]`, exposed to Tailwind v4 through `@theme inline` (the default Tailwind palette is removed, so components can only use tokens).
- `src/lib/palette.ts` mirrors the tokens for the canvas chart; `palette.test.ts` fails if the two drift or a pair drops below WCAG AA.
- First paint: the inline script in `index.html` sets `data-theme` from `?theme=light|dark` (also saved), else the saved choice, else `prefers-color-scheme`, else dark.
- Toggling re-colours the price chart in place (`applyOptions`, no re-creation). SVG charts use the CSS variables, so they follow automatically.
