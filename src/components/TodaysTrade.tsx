import type { Snapshot } from "../lib/api";
import { price } from "../lib/format";

/**
 * The one-glance answer: what setup the board is describing right now.
 *
 * Everything here is ALREADY COMPUTED elsewhere — plan.build() for the setup
 * and levels, bias_engine for the lean and conviction. This composes them into
 * one card rather than deriving anything new, deliberately: a second place
 * that computes a trade is a second place that can disagree with the first,
 * and then the board says two things at once.
 *
 * It states the setup, the levels it lives between, and the conviction. It
 * does NOT size the position, pick an expiry, or name a strike. Those depend
 * on account size and risk tolerance the app does not know, and a confident
 * screen printing "buy 5 contracts" would be inventing the part that actually
 * loses money.
 */
export function TodaysTrade({ snap }: { snap: Snapshot }) {
  const plan = snap.plan;
  const bias = snap.bias;
  const g = snap.gex;

  if (!plan) {
    return <div className="tt tt--empty">No plan — the snapshot has no trade layer.</div>;
  }

  const lean = bias?.label ?? "—";
  const conviction = bias?.conviction ?? "normal";
  const dir = (bias?.score ?? 0) >= 1 ? "up" : (bias?.score ?? 0) <= -1 ? "down" : "flat";

  return (
    <div className={`tt tt--${dir}`}>
      <div className="tt__top">
        <span className="tt__eyebrow">today · {snap.ticker}</span>
        <span className={`chip chip--${conviction === "high" ? "up" : conviction === "low" ? "warn" : "quiet"}`}>
          {conviction} conviction
        </span>
      </div>

      <div className="tt__lean num">{lean}</div>
      <p className="tt__headline">{plan.headline}</p>

      <div className="tt__levels">
        <Level label="floor" value={g.put_wall} tone="up" />
        <Level label="flip" value={g.gamma_flip} tone="flip" />
        <Level label="spot" value={g.spot} tone="now" />
        <Level label="ceiling" value={g.call_wall} tone="down" />
      </div>

      {plan.rows?.length > 0 && (
        <ul className="tt__rows">
          {plan.rows.slice(0, 3).map((r, i) => (
            <li key={i} className={`tt__row tt__row--${r.tone}`}>
              <span className="num tt__rowlvl">{price(r.level)}</span>
              <strong>{r.action}</strong>
              <em>{r.label}</em>
            </li>
          ))}
        </ul>
      )}

      <p className="tt__note">
        Setup and levels, not a position. Size, strike and expiry are yours —
        the board does not know your account.
      </p>
    </div>
  );
}

function Level({
  label, value, tone,
}: { label: string; value: number | null; tone: string }) {
  return (
    <div className="tt__lvl">
      <span className="tt__lvllabel">{label}</span>
      <span className={`num tt__lvlval tt__lvlval--${tone}`}>
        {value === null || value === undefined ? "none" : price(value)}
      </span>
    </div>
  );
}
