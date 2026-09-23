import { Link } from "react-router";
import { useNews } from "../../api/hooks";
import { relativeTime } from "../../lib/time";
import { SentimentDot } from "../SentimentDot";
import { Eyebrow } from "../ui/Chip";
import { QueryView } from "../ui/States";

/** Three headlines for this instrument, newest first. */
export function PanelNews({ instrument, name }: { instrument: string; name: string }) {
  const news = useNews(instrument, 3);
  return (
    <section aria-label="News">
      <div className="flex items-center gap-2">
        <Eyebrow>News</Eyebrow>
        <Link to={`/news?instrument=${encodeURIComponent(instrument)}`} className="ml-auto rounded text-xs no-underline hover:underline">
          More
        </Link>
      </div>
      <QueryView
        query={news}
        isEmpty={(items) => items.length === 0}
        empty={{ title: "No recent news", hint: `Nothing tagged to ${name} yet.` }}
        loadingLabel="Loading news…"
        className="p-4"
      >
        {(items) => (
          <ul className="mt-1">
            {items.slice(0, 3).map((n) => (
              <li key={n.id} className="py-1.5">
                <a href={n.url} target="_blank" rel="noopener noreferrer" className="group flex gap-2 rounded no-underline">
                  <SentimentDot value={n.sentiment} className="mt-1.5" />
                  <span className="line-clamp-2 text-sm leading-5 text-ink group-hover:text-accent">{n.title}</span>
                </a>
                <p className="pl-4 text-2xs text-ink-faint">
                  {n.source} · {relativeTime(n.published_at ?? n.fetched_at)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </QueryView>
    </section>
  );
}
