import { useState } from "react";
import type { Snapshot } from "../lib/api";
import { price } from "../lib/format";
import { Gauge } from "./Gauge";

/**
 * The call, the position, and the levels — one surface instead of three.
 *
 * Today, Bias and Level Map each stated the same conclusion in a different
 * shape: Today said NEUTRAL / RANGE, Bias said NEUTRAL / RANGE −1.8 with its
 * signals, Level Map listed the levels Today had already shown. Three panels
 * of equal visual weight repeating one answer is how a board stops having a
 * focal point, and it invites the question of which one is authoritative.
 *
 * The reasoning is NOT dropped — it moves behind a toggle. A call you cannot
 * interrogate is a number to obey, and the whole design of this engine is that
 * every signal carries its why. Collapsed by default, one click away, and the
 * count is on the button so you know how much is hidden.
 */
export function TheCall({ snap }: { snap: Snapshot }) {
  const [showWhy, setShowWhy] = useState(false);
  const { gex: g, bias, plan } = snap;

  const dir = (bias?.score ?? 0) >= 1 ? "up" : (bias?.score ?? 0) <= -1 ? "down" : "flat";
  // Signals that actually voted. A zero-weight row is context, not a reason,
  // and padding the count with them would overstate how much is behind the
  // toggle.
  const voted = (bias?.signals ?? []).filter((s) => s.weight > 0);
  const context = (bias?.signals ?? []).filter((s) => s.weight === 0);

  return (
    <div className={`call call--${dir}`}>
      <div className="call__top">
        <span className="call__eyebrow">today · {snap.ticker}</span>
        <span className={`chip chip--${
          bias?.conviction === "high" ? "up"
            : bias?.conviction === "reduced" || bias?.conviction === "low" ? "warn"
            : "quiet"}`}>
          {bias?.conviction ?? "normal"} conviction
        </span>
      </div>

      <div className="call__lean">{bias?.label ?? "—"}</div>
      {plan?.headline && <p className="call__headline">{plan.headline}</p>}

      {/* The gauge answers the next question after "what is the lean": where
          am I standing relative to the levels that produced it. */}
      <Gauge gex={g} />

      <div className="call__levels">
        <Lv label="floor" v={g.put_wall} tone="up" note="put wall" spot={g.spot} />
        <Lv label="flip" v={g.gamma_flip} tone="flip" note="regime line" spot={g.spot} />
        <Lv label="ceiling" v={g.call_wall} tone="down" note="call wall" spot={g.spot} />
      </div>

      {plan?.rows?.length > 0 && (
        <ul className="call__actions">
          {plan.rows.slice(0, 3).map((r, i) => (
            <li key={i} className={`call__act call__act--${r.tone}`}>
              <span className="num call__actlvl">{price(r.level)}</span>
              <strong>{r.action}</strong>
              <em>{r.label}</em>
            </li>
          ))}
        </ul>
      )}

      <button
        className="call__why"
        onClick={() => setShowWhy((v) => !v)}
        aria-expanded={showWhy}
      >
        {showWhy ? "hide" : "why"} · {voted.length} signal{voted.length === 1 ? "" : "s"}
        {bias?.score !== undefined && <span className="num"> · score {bias.score}</span>}
      </button>

      {showWhy && (
        <div className="call__signals">
          {voted.map((s) => (
            <div key={s.name} className="call__sig">
              <div className="call__sighead">
                <i className={`call__lean call__lean--${
                  s.lean > 0 ? "up" : s.lean < 0 ? "down" : "flat"}`}>
                  {s.lean > 0 ? "▲" : s.lean < 0 ? "▼" : "■"}
                </i>
                <span className="call__signame">{s.name}</span>
                <span className="num call__sigw">{s.weight.toFixed(2)}</span>
              </div>
              <p className="call__sigwhy">{s.reason}</p>
            </div>
          ))}

          {/* Zero-weight rows are shown too, but marked as context so the
              distinction between "this voted" and "this is background" is not
              left to the reader to infer from a weight column. */}
          {context.length > 0 && (
            <div className="call__ctx">
              <div className="call__ctxk">context — no vote</div>
              {context.map((s) => (
                <p key={s.name} className="call__sigwhy">
                  <strong>{s.name}.</strong> {s.reason}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="call__note">
        Setup and levels, not a position. Size, strike and expiry are yours —
        the board does not know your account.
      </p>
    </div>
  );
}

function Lv({
  label, v, tone, note, spot,
}: { label: string; v: number | null; tone: string; note: string; spot: number }) {
  const away = v !== null && spot ? ((v - spot) / spot) * 100 : null;
  return (
    <div className="call__lv">
      <span className="call__lvk">{label}</span>
      <span className={`num call__lvv call__lvv--${tone}`}>
        {v === null ? "none" : price(v)}
      </span>
      <span className="call__lvn">
        {note}
        {away !== null && ` · ${away > 0 ? "+" : "−"}${Math.abs(away).toFixed(2)}%`}
      </span>
    </div>
  );
}
