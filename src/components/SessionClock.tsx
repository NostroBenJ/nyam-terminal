import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Sessions } from "../lib/api";

/**
 * Where you are in the trading day — ticking every second, never delayed.
 *
 * This used to render `now_et` straight from the engine and re-sync every 30s,
 * so the displayed clock was a still frame up to half a minute old and the
 * open/close countdown jumped in 30-second steps. On a board you trade the
 * open from, a clock that is silently 29 seconds behind is worse than no clock.
 *
 * Now the engine is the source of the CALENDAR (holidays, early closes, the
 * window definitions, your traded span) and of a single time ANCHOR. The
 * seconds are counted locally from that anchor, and which sessions and windows
 * are active is derived locally too — so the display is exact between syncs and
 * a slow or stalled engine shows a stale-clock warning rather than a wrong time.
 */

const SYNC_MS = 60_000;
/** Past this without a successful sync, stop trusting the derived state. */
const STALE_AFTER_MS = 150_000;

const secOfDay = (hhmmss: string): number => {
  const [h = 0, m = 0, s = 0] = hhmmss.split(":").map(Number);
  return h * 3600 + m * 60 + s;
};

const fmt = (sec: number): string => {
  const t = ((sec % 86400) + 86400) % 86400;
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = Math.floor(t % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};

/** Inclusive-start, exclusive-end, wrapping past midnight when start > end. */
const inWindow = (nowSec: number, start: string, end: string): boolean => {
  const a = secOfDay(start);
  const b = secOfDay(end);
  return a <= b ? nowSec >= a && nowSec < b : nowSec >= a || nowSec < b;
};

export function SessionClock({ compact }: { compact?: boolean }) {
  const [s, setS] = useState<Sessions | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Re-render each second; the actual time is computed from the anchor. */
  const [, tick] = useState(0);
  const anchor = useRef<{ etSec: number; at: number } | null>(null);

  useEffect(() => {
    let dead = false;
    const pull = () =>
      api
        .sessions()
        .then((r) => {
          if (dead) return;
          // Anchor the local clock to the engine's ET reading at the instant
          // the response landed, then count seconds off it.
          anchor.current = { etSec: secOfDay(r.now_et), at: Date.now() };
          setS(r);
          setError(null);
        })
        .catch((e) => !dead && setError(String(e)));
    void pull();
    const sync = setInterval(pull, SYNC_MS);
    const beat = setInterval(() => tick((n) => n + 1), 1000);
    return () => {
      dead = true;
      clearInterval(sync);
      clearInterval(beat);
    };
  }, []);

  const live = useMemo(() => {
    if (!s || !anchor.current) return null;
    const elapsedMs = Date.now() - anchor.current.at;
    const nowSec = anchor.current.etSec + elapsedMs / 1000;

    const sessions = s.sessions.map((x) => ({
      ...x,
      // US sessions don't run on a closed day; FX sessions keep their own clock.
      active: x.kind === "us" && !s.trading_day
        ? false
        : inWindow(nowSec, x.start, x.end),
    }));
    const windows = s.windows.map((w) => ({
      ...w,
      active: s.trading_day && inWindow(nowSec, w.start, w.end),
    }));
    const myWindow = {
      ...s.my_window,
      active: s.trading_day && inWindow(nowSec, s.my_window.start, s.my_window.end),
    };

    // Countdown recomputed locally so it ticks by the second.
    const closeAt = s.early_close ? "13:00" : "16:00";
    const openAt = s.my_window.start;
    let phase: "open" | "pre" | "closed" = "closed";
    let untilSec: number | null = null;
    let untilLabel: string | null = null;
    if (s.trading_day && inWindow(nowSec, openAt, closeAt)) {
      phase = "open";
      untilSec = secOfDay(closeAt) - nowSec;
      untilLabel = "close";
    } else if (s.trading_day && nowSec < secOfDay(openAt)) {
      phase = "pre";
      untilSec = secOfDay(openAt) - nowSec;
      untilLabel = "open";
    }

    return {
      time: fmt(nowSec),
      sessions,
      windows,
      myWindow,
      phase,
      untilSec,
      untilLabel,
      stale: elapsedMs > STALE_AFTER_MS,
    };
  }, [s, anchor.current?.at, Math.floor(Date.now() / 1000)]);

  if (error && !s) return <p className="empty">Clock unavailable — {error}</p>;
  if (!s || !live) return <p className="empty">…</p>;

  const phaseTone =
    live.phase === "open" ? "up" : live.phase === "pre" ? "warn" : "quiet";
  const phaseLabel =
    live.phase === "open" ? "MARKET OPEN" : live.phase === "pre" ? "PRE-MARKET" : "CLOSED";

  const countdown =
    live.untilSec === null
      ? null
      : `${Math.floor(live.untilSec / 3600)}h ${String(
          Math.floor((live.untilSec % 3600) / 60)
        ).padStart(2, "0")}m ${String(Math.floor(live.untilSec % 60)).padStart(2, "0")}s to ${live.untilLabel}`;

  return (
    <div className="clock">
      <div className="clock__head">
        <span className={`chip chip--${phaseTone}`}>{phaseLabel}</span>
        <span className="clock__time num">{live.time}</span>
        <span className="clock__tz">ET</span>
        {countdown && <span className="clock__count num">{countdown}</span>}
      </div>

      {/* A clock that has lost contact with the engine must say so rather than
          keep counting confidently off a stale anchor. */}
      {live.stale && (
        <p className="clock__stale">
          Clock hasn’t re-synced with the engine in over{" "}
          {Math.round(STALE_AFTER_MS / 1000)}s — treat the time as approximate.
        </p>
      )}

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
        {live.sessions.map((x) => (
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
          {live.windows.map((w) => (
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

      <div className={`mywin${live.myWindow.active ? " mywin--on" : ""}`}>
        <span className="mywin__label">
          Your window {live.myWindow.start}–{live.myWindow.end}
        </span>
        <span className="mywin__note">{live.myWindow.note}</span>
      </div>
    </div>
  );
}
