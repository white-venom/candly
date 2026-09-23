import { useMemo } from "react";
import { Link, useSearchParams } from "react-router";
import { useInstruments, useNews } from "../api/hooks";
import type { NewsItem } from "../api/types";
import { InstrumentOptions } from "../components/InstrumentOptions";
import { SentimentDot } from "../components/SentimentDot";
import { Page } from "../components/shell/Page";
import { Chip } from "../components/ui/Chip";
import { Segmented, SelectField } from "../components/ui/Controls";
import { EmptyState, QueryView } from "../components/ui/States";
import { humanize, sentimentOf, type Sentiment } from "../lib/format";
import { chartPath } from "../lib/routes";
import { formatWhenIST, relativeTime } from "../lib/time";

type SentimentFilter = "all" | Exclude<Sentiment, "unknown">;

const SENTIMENTS: { value: SentimentFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "positive", label: "Positive" },
  { value: "negative", label: "Negative" },
  { value: "neutral", label: "Neutral" },
];

function readSentiment(value: string | null): SentimentFilter {
  return SENTIMENTS.find((s) => s.value === value)?.value ?? "all";
}

function sentence(value: string): string {
  const h = humanize(value);
  return h[0].toUpperCase() + h.slice(1);
}

function Item({ item }: { item: NewsItem }) {
  const when = item.published_at ?? item.fetched_at;
  return (
    <article className="flex gap-3 border-b border-line/60 px-4 py-3 last:border-b-0">
      <SentimentDot value={item.sentiment} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <a href={item.url} target="_blank" rel="noopener noreferrer" className="min-w-0 flex-1 rounded text-sm font-medium text-ink no-underline hover:text-accent">
            {item.title}
          </a>
          <time
            className="shrink-0 text-xs text-ink-faint tabular-nums"
            title={`${item.published_at === null ? "Fetched" : "Published"} ${formatWhenIST(when)} IST`}
          >
            {relativeTime(when)}
          </time>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-faint">
          <span>{item.source}</span>
          {item.event_type && <Chip>{sentence(item.event_type)}</Chip>}
          {item.instruments.map((id) => (
            <Link key={id} to={chartPath(id, "1D")} className="rounded no-underline">
              <Chip tone="accent">{id.split(":").pop()}</Chip>
            </Link>
          ))}
        </div>
        {item.summary && <p className="mt-1 line-clamp-1 text-xs text-ink-muted">{item.summary}</p>}
      </div>
    </article>
  );
}

export function NewsPage() {
  const [params, setParams] = useSearchParams();
  const instrument = params.get("instrument") || null;
  const sentiment = readSentiment(params.get("sentiment"));
  const event = params.get("event") || null;
  const instruments = useInstruments();
  const news = useNews(instrument, 100);

  const events = useMemo(() => [...new Set((news.data ?? []).map((n) => n.event_type).filter((e): e is string => Boolean(e)))].sort(), [news.data]);
  const shown = (news.data ?? []).filter(
    (n) => (sentiment === "all" || sentimentOf(n.sentiment) === sentiment) && (!event || n.event_type === event),
  );

  const update = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <Page
      title="News"
      description="Tagged, timestamped market news; times in IST"
      toolbar={
        <>
          <SelectField label="Instrument" value={instrument ?? ""} onChange={(e) => update("instrument", e.target.value)}>
            <option value="">All news</option>
            <InstrumentOptions instruments={instruments.data ?? []} />
          </SelectField>
          <Segmented label="Sentiment" value={sentiment} onChange={(v) => update("sentiment", v === "all" ? null : v)} options={SENTIMENTS} />
          <SelectField label="Event" value={event ?? ""} onChange={(e) => update("event", e.target.value)} disabled={events.length === 0}>
            <option value="">Any event</option>
            {events.map((e) => (
              <option key={e} value={e}>
                {sentence(e)}
              </option>
            ))}
          </SelectField>
        </>
      }
    >
      <QueryView
        query={news}
        isEmpty={(items) => items.length === 0}
        empty={
          instrument
            ? { title: "No news for this instrument yet", hint: "Headlines appear as the feeds mention it." }
            : { title: "No news yet", hint: "The RSS poller fills this feed." }
        }
        loadingLabel="Loading news…"
        className="rounded-lg border border-line bg-surface"
      >
        {() =>
          shown.length === 0 ? (
            <EmptyState title="Nothing matches these filters" className="rounded-lg border border-line bg-surface" />
          ) : (
            <div className="rounded-lg border border-line bg-surface">
              {shown.map((item) => (
                <Item key={item.id} item={item} />
              ))}
            </div>
          )
        }
      </QueryView>
    </Page>
  );
}
