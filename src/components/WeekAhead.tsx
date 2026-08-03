import { useCallback, useEffect, useState } from "react";
import { api, type Calendar, type CalEvent } from "../lib/api";
import { Empty } from "./Panel";

/**
 * What's COMING this week — scheduled macro releases and upcoming earnings.
 *
 * The counterpart to the news rail, and the distinction is the point. RSS tells
 * you what already published; this tells you what is ahead. "CPI landed an hour
 * ago" and "CPI drops Wednesday 08:30" call for opposite trades, and until this
 * panel existed the app could only see the first.
 *
 * High-impact rows are the ones that reprice the whole index — those get the
 * emphasis; the rest is context you can scan past.
 */

function impactClass(e: CalEvent): string {
  return e.rank >= 3 ? "high" : e.impact === "Medium" ? "med" : "low";
}

export function WeekAhead() {
  const [cal, setCal] = useState<Calendar | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [onlyBig, setOnlyBig] = useState(true);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      setCal(await api.calendar(refresh));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !cal) return <Empty>Calendar unavailable — {error}</Empty>;
  if (!cal) return <Empty>Loading calendar…</Empty>;

  const days = cal.days
    .map((d) => ({ ...d, events: onlyBig ? d.events.filter((e) => e.rank >= 3) : d.events }))
    .filter((d) => d.events.length > 0);

  return (
    <div className="week">
      <div className="week__bar">
        <div className="news__toggles">
          <button
            className={`pchart__iv${onlyBig ? " pchart__iv--on" : ""}`}
            onClick={() => setOnlyBig(true)}
            title="Only releases that reprice the whole index"
          >
            high impact
          </button>
          <button
            className={`pchart__iv${!onlyBig ? " pchart__iv--on" : ""}`}
            onClick={() => setOnlyBig(false)}
          >
            everything
          </button>
        </div>
        <div className="news__meta">
          <span className="news__count">
            {cal.counts.econ} econ · {cal.counts.earnings_upcoming} earnings
          </span>
          <button className="btn" onClick={() => void load(true)} disabled={busy}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* A calendar served from cache after an upstream failure must say so —
          an old schedule read as current is how you miss a release. */}
      {cal.stale && (
        <div className="week__stale">
          Showing the last good calendar — {Object.values(cal.errors)[0]}
        </div>
      )}

      {cal.headline && (
        <div className="week__next">
          <span className="week__nextlabel">NEXT HIGH-IMPACT</span>
          <span className="week__nexttitle">{cal.headline.title}</span>
          <span className="week__nextwhen num">
            {new Date(`${cal.headline.date}T12:00:00`).toLocaleDateString(undefined, {
              weekday: "short",
            })}{" "}
            {cal.headline.time}
          </span>
        </div>
      )}

      {days.length === 0 ? (
        <Empty>
          {onlyBig
            ? "No high-impact releases scheduled this week. Switch to “everything” for the full calendar."
            : "No scheduled events returned."}
        </Empty>
      ) : (
        <div className="week__days">
          {days.map((d) => (
            <div key={d.date} className={`wday${d.is_today ? " wday--today" : ""}${d.is_past ? " wday--past" : ""}`}>
              <div className="wday__head">
                <span className="wday__dow">{d.weekday}</span>
                <span className="wday__date num">{d.date.slice(5)}</span>
                {d.is_today && <span className="chip chip--warn">today</span>}
              </div>
              <ul className="wday__list">
                {d.events.map((e, i) => (
                  <li key={`${e.title}-${i}`} className={`wev wev--${impactClass(e)}`}>
                    <span className="wev__time num">{e.time || "—"}</span>
                    <span className="wev__title">
                      {e.kind === "earnings" && <span className="wev__tag">ER</span>}
                      {e.title}
                    </span>
                    {e.forecast && <span className="wev__fc num">{e.forecast}</span>}
                    {e.previous && <span className="wev__prev num">prev {e.previous}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {cal.upcoming_earnings.length > 0 && (
        <div className="week__earnings">
          <h4 className="panel-title">Upcoming earnings</h4>
          <ul className="wearn">
            {cal.upcoming_earnings.map((e) => (
              <li key={e.ticker}>
                <span className="wearn__tkr">{e.ticker}</span>
                <span className="wearn__date num">{e.date}</span>
                <span className="wearn__out num">
                  {e.days_out !== undefined ? `${e.days_out}d` : ""}
                </span>
                <span className="wearn__fc">{e.forecast}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="disclaimer">
        Scheduled times, not outcomes — {cal.source}. A release can move or land
        early; the news rail is what tells you it actually happened.
      </p>
    </div>
  );
}
