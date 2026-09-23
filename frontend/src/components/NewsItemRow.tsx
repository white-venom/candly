import type { NewsItem } from "../api/types";
import { fmtNum, humanize } from "../lib/format";
import { formatDateTimeIST } from "../lib/time";
import { Badge, type Tone } from "./ui";

// A small dead zone so near-zero scores read as neutral.
function sentimentLook(value: number): { tone: Tone; label: string } {
  if (value > 0.1) return { tone: "up", label: "▲ positive" };
  if (value < -0.1) return { tone: "down", label: "▼ negative" };
  return { tone: "neutral", label: "neutral" };
}

function Sentiment({ item }: { item: NewsItem }) {
  if (item.sentiment === null) return <Badge>sentiment —</Badge>;
  const { tone, label } = sentimentLook(item.sentiment);
  const value = `${item.sentiment > 0 ? "+" : ""}${fmtNum(item.sentiment, 2)}`;
  return (
    <Badge tone={tone} title={item.sentiment_method ? `scored by ${item.sentiment_method}` : undefined}>
      {label} {value}
    </Badge>
  );
}

export function NewsItemRow({ item, showInstruments = false }: { item: NewsItem; showInstruments?: boolean }) {
  return (
    <article className="border-b border-line py-2 last:border-b-0">
      <a href={item.url} target="_blank" rel="noopener noreferrer" className="font-medium text-ink no-underline hover:text-accent hover:underline">
        {item.title}
        <span aria-hidden="true" className="text-ink-faint"> ↗</span>
      </a>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
        <span>{item.source}</span>
        <Sentiment item={item} />
        {item.event_type && <Badge tone="accent">{humanize(item.event_type)}</Badge>}
        <span>Published {item.published_at !== null ? `${formatDateTimeIST(item.published_at)} IST` : "unknown"}</span>
        <span>Fetched {formatDateTimeIST(item.fetched_at)} IST</span>
      </div>
      {item.summary && <p className="mt-1 text-ink-muted">{item.summary}</p>}
      {showInstruments && item.instruments.length > 0 && (
        <p className="mt-1 flex flex-wrap gap-1">
          {item.instruments.map((id) => (
            <Badge key={id}>{id}</Badge>
          ))}
        </p>
      )}
    </article>
  );
}
