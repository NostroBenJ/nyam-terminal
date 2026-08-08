import { useCallback, useEffect, useState } from "react";
import { api, type Health, type Sessions, type Snapshot } from "../lib/api";
import { clock12, hhmm12 } from "../lib/format";

/**
 * Session, event risk and API budget on one line instead of three panels.
 *
 * Each of these is a thing you glance at once and then ignore for hours. Given
 * a full panel each — with a title, a subtitle and an age chip — they occupied
 * roughly a third of the right column at the same visual weight as the call
 * itself. Weight should follow how often you actually look at something.
 *
 * Nothing is removed. The session detail still lives in its own panel for when
 * you want it; this is the glance.
 */
export function StatusStrip({ snap }: { snap: Snapshot }) {
  const [s, setS] = useState<Sessions | null>(null);
  const [h, setH] = useState<Health | null>(null);

  const load = useCallback(async () => {
    try {
      const [sess, health] = await Promise.all([api.sessions(), api.health()]);
      setS(sess);
      setH(health);
    } catch {
      /* the strip is a glance; a failed fetch leaves it blank rather than
         throwing a banner over a board that is otherwise fine */
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(load, 30_000);
    return () => window.clearInterval(t);
  }, [load]);

  const active = s?.sessions?.find((x) => x.active);
  const risk = snap.news;
  const budget = h?.uw_budget;

  return (
    <div className="strip">
      <span className="strip__cell">
        <i className={`strip__dot strip__dot--${s?.trading_day ? "on" : "off"}`} />
        {s?.now_et ? clock12(secOfDay(s.now_et)) : "—"}
        <em>ET</em>
      </span>

      <span className="strip__cell">
        {active ? active.label : s?.trading_day === false ? "market closed" : "between sessions"}
        {active && <em>{hhmm12(active.start)}–{hhmm12(active.end)}</em>}
      </span>

      {/* Event risk earns a spot only when there IS one. A permanent "no events"
          chip is a line of screen saying nothing. */}
      {risk?.high_impact && (
        <span className="strip__cell strip__cell--warn">
          <i className="strip__dot strip__dot--warn" />
          {risk.headline || "high-impact event today"}
        </span>
      )}

      <span className="strip__sp" />

      {budget && (
        <span
          className="strip__cell strip__cell--quiet"
          title={`${budget.used} of ${budget.limit} Unusual Whales requests used today`}
        >
          UW <span className="num">{budget.pct}%</span>
        </span>
      )}
      <span className="strip__cell strip__cell--quiet">
        mix <span className="num">{snap.track?.mix ?? "—"}</span>
      </span>
    </div>
  );
}

const secOfDay = (hhmmss: string): number => {
  const [h = 0, m = 0, sec = 0] = hhmmss.split(":").map(Number);
  return h * 3600 + m * 60 + sec;
};
