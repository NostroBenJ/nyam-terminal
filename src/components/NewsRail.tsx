import { useCallback, useEffect, useState } from "react";
import { api, type News, type NewsItem } from "../lib/api";
import { Empty } from "./Panel";

/**
 * The news rail.
 *
 * Structurally modelled on WorldMonitor — categorized, per-source freshness,
 * relevance filtering — but reading our own feeds through our own parser.
 * WorldMonitor is AGPL-3.0; borrowing the shape is fine, vendoring the code
 * would reach this whole application.
 *
 * Two rules it holds to:
 *  - Failed sources are NAMED. A rail that quietly drops from 12 feeds to 3
 *    looks exactly like a quiet news day, and those are very different things
 *    to be looking at before the open.
 *  - An undated item is labelled "no date", never stamped with now().
 */

const CATEGORY_LABEL: Record<string, string> = {
  "central-bank": "central bank",
  "econ-data": "econ data",
  regulatory: "regulatory",
  markets: "markets",
  ticker: "ticker",
};

/**
 * Several feeds (the Fed's among them) set description == title. Rendering
 * both prints the headline twice and pushes real items off the screen.
 */
function usefulSummary(item: NewsItem): string | null {
  const s = item.summary?.trim();
  if (!s) return null;
  const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9]/g, "");
  const t = norm(item.title);
  const b = norm(s);
  if (b === t || b.startsWith(t) || t.startsWith(b)) return null;
  return s;
}

function age(item: NewsItem): string {
  if (item.age_hours === null) return "no date";
  const h = item.age_hours;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) return `${Math.round(h)}h`;
  return `${Math.round(h / 24)}d`;
}

export function NewsRail({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const [news, setNews] = useState<News | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sort, setSort] = useState<"relevance" | "latest">("relevance");
  const [onlyRelevant, setOnlyRelevant] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setBusy(true);
      setError(null);
      try {
        setNews(await api.news(ticker, sort, refresh));
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(false);
      }
    },
    [ticker, sort]
  );

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !news) return <Empty>News unavailable — {error}</Empty>;
  if (!news) return <Empty>Loading feeds…</Empty>;

  const failed = Object.entries(news.errors);
  const live = Object.values(news.sources).filter((s) => s.n > 0).length;
  const items = onlyRelevant
    ? news.items.filter((i) => i.ticker_match || i.macro_terms.length > 0)
    : news.items;

  return (
    <div className="news">
      <div className="news__bar">
        <div className="news__toggles">
          <button
            className={`pchart__iv${sort === "relevance" ? " pchart__iv--on" : ""}`}
            onClick={() => setSort("relevance")}
            title="Relevance decayed by age — what matters now"
          >
            relevant
          </button>
          <button
            className={`pchart__iv${sort === "latest" ? " pchart__iv--on" : ""}`}
            onClick={() => setSort("latest")}
            title="Strict reverse-chronological"
          >
            latest
          </button>
          <button
            className={`pchart__iv${onlyRelevant ? " pchart__iv--on" : ""}`}
            onClick={() => setOnlyRelevant((v) => !v)}
            title={`Only items naming ${ticker} or a macro driver`}
          >
            filter
          </button>
        </div>
        <div className="news__meta">
          <span className="news__count">
            {items.length} of {news.items.length} · {live}/{Object.keys(news.sources).length} feeds
          </span>
          <button className="btn" onClick={() => void load(true)} disabled={busy}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {failed.length > 0 && (
        <div className="news__failed">
          {failed.length} source{failed.length === 1 ? "" : "s"} unavailable:{" "}
          {failed.map(([id, msg]) => (
            <span key={id} className="news__failed-one">
              {news.sources[id]?.name ?? id} ({msg})
            </span>
          ))}
        </div>
      )}

      {items.length === 0 ? (
        <Empty>
          Nothing matched. {onlyRelevant && "Try turning the filter off — "}
          {news.total_before_dedupe} items were fetched.
        </Empty>
      ) : (
        <ul className="news__list">
          {items.map((it, i) => (
            <li key={`${it.link}-${i}`} className={`newsitem newsitem--${it.tier}`}>
              <div className="newsitem__head">
                <span className={`newsitem__age${it.age_hours === null ? " newsitem__age--none" : ""}`}>
                  {age(it)}
                </span>
                <span className="newsitem__source">{it.source}</span>
                <span className="newsitem__cat">
                  {CATEGORY_LABEL[it.category] ?? it.category}
                </span>
                {it.ticker_match && <span className="chip chip--warn">{news.ticker}</span>}
                {it.tier === "primary" && <span className="chip chip--quiet">official</span>}
              </div>
              <a
                className="newsitem__title"
                href={it.link || undefined}
                target="_blank"
                rel="noreferrer noopener"
              >
                {it.title}
              </a>
              {!compact && usefulSummary(it) && (
                <p className="newsitem__sum">{usefulSummary(it)}</p>
              )}
              {it.macro_terms.length > 0 && (
                <div className="newsitem__terms">
                  {it.macro_terms.map((t) => (
                    <span key={t} className="newsitem__term">{t}</span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
