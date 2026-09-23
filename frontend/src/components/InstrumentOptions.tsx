import type { Instrument } from "../api/types";
import { groupInstruments } from "../lib/instruments";

/** `<option>`s for an instrument `<select>`, in watchlist sections. */
export function InstrumentOptions({ instruments }: { instruments: Instrument[] }) {
  return groupInstruments(instruments).map((g) => (
    <optgroup key={g.key} label={g.label}>
      {g.items.map((i) => (
        <option key={i.id} value={i.id}>
          {i.name} ({i.symbol})
        </option>
      ))}
    </optgroup>
  ));
}
