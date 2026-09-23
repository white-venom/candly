import type { Instrument } from "../api/types";
import { groupByExchange } from "../lib/instruments";

/** Options grouped by exchange, for any <select>. */
export function InstrumentOptions({ instruments }: { instruments: Instrument[] }) {
  return (
    <>
      {groupByExchange(instruments).map(([exchange, items]) => (
        <optgroup key={exchange} label={exchange}>
          {items.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name} ({i.symbol})
            </option>
          ))}
        </optgroup>
      ))}
    </>
  );
}
