import { useSearchParams } from "react-router";
import { useInstruments, useNews } from "../api/hooks";
import { InstrumentOptions } from "../components/InstrumentSelect";
import { NewsItemRow } from "../components/NewsItemRow";
import { QueryView } from "../components/States";
import { SelectField } from "../components/ui";

export function NewsPage() {
  const [params, setParams] = useSearchParams();
  const instrument = params.get("instrument") || null;
  const instruments = useInstruments();
  const news = useNews(instrument, 100);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="text-lg font-semibold text-ink">News</h1>
        <SelectField
          label="Instrument"
          value={instrument ?? ""}
          onChange={(e) => setParams(e.target.value ? { instrument: e.target.value } : {})}
        >
          <option value="">All news</option>
          <InstrumentOptions instruments={instruments.data ?? []} />
        </SelectField>
        <p className="pb-1.5 text-xs text-ink-faint">
          Times in IST. Backtests may only use an item from its fetched time.
        </p>
      </div>
      <QueryView
        query={news}
        isEmpty={(items) => items.length === 0}
        empty={instrument ? "No news logged for this instrument yet." : "No news logged yet — the RSS poller fills this."}
        loadingLabel="Loading news…"
      >
        {(items) => (
          <div className="rounded-lg border border-line bg-surface px-3">
            {items.map((item) => (
              <NewsItemRow key={item.id} item={item} showInstruments={!instrument} />
            ))}
          </div>
        )}
      </QueryView>
    </div>
  );
}
