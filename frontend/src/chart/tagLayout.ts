export type Slot = { y: number; fixed: boolean };

/**
 * Places axis tags `height` tall so none overlap and their top-to-bottom order matches their prices.
 * Fixed tags (the last price, the stop) stay exactly where they are. The others fill the gaps between
 * fixed tags; when a gap is too small for all of them, the ones later in the input (less important)
 * get no tag. Returns each input's centre, or null when it is left out.
 */
export function placeTags(slots: Slot[], height: number, maxY: number): (number | null)[] {
  const out: (number | null)[] = slots.map((s) => (s.fixed ? s.y : null));
  const fixedYs = slots.filter((s) => s.fixed).map((s) => s.y).sort((a, b) => a - b);
  const bounds = [-Infinity, ...fixedYs, Infinity];

  for (let g = 0; g + 1 < bounds.length; g++) {
    const lo = bounds[g] === -Infinity ? height / 2 : bounds[g] + height;
    const hi = bounds[g + 1] === Infinity ? maxY - height / 2 : bounds[g + 1] - height;
    if (hi < lo) continue;
    const members = slots
      .map((s, i) => ({ ...s, i }))
      .filter((s) => !s.fixed && s.y >= bounds[g] && s.y < bounds[g + 1]);
    const capacity = Math.floor((hi - lo) / height) + 1;
    const kept = members.slice(0, capacity).sort((a, b) => a.y - b.y);

    const ys = kept.map((s) => Math.min(hi, Math.max(lo, s.y)));
    for (let k = 1; k < ys.length; k++) ys[k] = Math.max(ys[k], ys[k - 1] + height);
    for (let k = ys.length - 1; k >= 0; k--) ys[k] = Math.min(ys[k], k === ys.length - 1 ? hi : ys[k + 1] - height);
    kept.forEach((s, k) => (out[s.i] = ys[k]));
  }
  return out;
}
