import { useEffect, useState } from "react";
import { api, type Sessions } from "../lib/api";

/**
 * Where you are in the trading day.
 *
 * Volatility is not uniform: the London/NY overlap is where it concentrates,
 * the lunch lull is where breakouts fail, and 0DTE gamma pins hardest into the
 * bell. This changes position size, not just curiosity — which is the bar a
 * panel has to clear to earn space here.
 *
 * The engine owns the calendar (holidays, early closes, the traded window from
 * config) so the clock can never disagree with the rule the bias is graded
 * under. This ticks the seconds locally and re-syncs on a slow interval.
 */

const SYNC_MS = 30_000;

export function SessionClock({ compact }: { compact?: boolean }) {
  const [s, setS] = useState<Sessions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    const pull = () =>
      api
        .sessions()
        .then((r) => !dead && setS(r))
        .catch((e) => !dead && setError(String(e)));
    void pull();
    const t = setInterval(pull, SYNC_MS);
    return () => {
      dead = true;
      clearInterval(t);
    };
  }, []);

  if (error && !s) return <p className="empty">Clock unavailable — {error}</p>;
  if (!s) return <p className="empty">…</p>;

  const phaseTone =
    s.phase === "open" ? "up" : s.phase === "pre" ? "warn" : "quiet";
  const phaseLabel =
    s.phase === "open" ? "MARKET OPEN"
      : s.phase === "pre" ? "PRE-MARKET"
      : "CLOSED";

  const countdown =
    s.minutes_until === null
      ? null
      : `${Math.floor(s.minutes_until / 60)}h ${s.minutes_until % 60}m to ${s.until_label}`;

  return (
    <div className="clock">
      <div className="clock__head">
        <span className={`chip chip--${phaseTone}`}>{phaseLabel}</span>
        <span className="clock__time num">{s.now_et}</span>
        <span className="clock__tz">ET</span>
        {countdown && <span className="clock__count">{countdown}</span>}
      </div>

      {/* A closed market always says WHY — "Weekend" and "Thanksgiving" are
          different reasons to see a flat tape. */}
      {!s.trading_day && (
        <p className="clock__closed">
          {s.closed_reason} — {s.weekday} {s.date}
        </p>
      )}
      {s.early_close && (
        <p className="clock__early">
          Early close {s.early_close} ET — {s.early_close_name}
        </p>
      )}

      <div className="clock__sessions">
        {s.sessions.map((x) => (
          <div
            key={x.id}
            className={`sess${x.active ? " sess--on" : ""} sess--${x.kind}`}
            title={`${x.label} ${x.start}–${x.end} ET`}
          >
            <span className="sess__label">{x.label}</span>
            <span className="sess__hours num">
              {x.start}–{x.end}
            </span>
          </div>
        ))}
      </div>

      {!compact && (
        <div className="clock__windows">
          {s.windows.map((w) => (
            <div key={w.id} className={`win${w.active ? " win--on" : ""}`}>
              <div className="win__head">
                <span className="win__label">{w.label}</span>
                <span className="win__hours num">
                  {w.start}–{w.end}
                </span>
              </div>
              <p className="win__note">{w.note}</p>
            </div>
          ))}
        </div>
      )}

      <div className={`mywin${s.my_window.active ? " mywin--on" : ""}`}>
        <span className="mywin__label">
          Your window {s.my_window.start}–{s.my_window.end}
        </span>
        <span className="mywin__note">{s.my_window.note}</span>
      </div>
    </div>
  );
}
