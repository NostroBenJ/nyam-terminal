import type { DarkPoolPrint } from "../lib/api";
import { int, money, price } from "../lib/format";
import { Empty } from "./Panel";

/**
 * Dark pool prints, with where each one sat in the spread.
 *
 * The placement is the point. A block printing at the ask is a buyer lifting;
 * the same size at the bid is a seller hitting. Without that column every row
 * reads as "someone traded a lot", which is not information.
 *
 * `lean` is an INFERENCE and the panel says so. Dark pool prints carry no
 * aggressor flag, so side is read from position in the spread — standard
 * practice, still a guess. Mid prints are shown as mid rather than pushed to a
 * side, and a missing NBBO shows nothing rather than a fabricated lean.
 */
export function DarkPool({ prints }: { prints: DarkPoolPrint[] | null }) {
  if (!prints || prints.length === 0) {
    return (
      <Empty>
        No dark pool prints. Off-exchange volume is reported on a delay and
        thins out overnight — an empty tape here outside market hours is
        normal, not a failure.
      </Empty>
    );
  }

  const live = prints.filter((p) => !p.canceled);
  const notional = live.reduce((s, p) => s + (p.premium || 0), 0);
  const buys = live.filter((p) => p.lean === "buy").length;
  const sells = live.filter((p) => p.lean === "sell").length;
  const canceled = prints.length - live.length;

  return (
    <div className="dp">
      <div className="dp__head">
        <div className="dp__stat">
          <span className="track__label">prints</span>
          <span className="dp__num num">{live.length}</span>
        </div>
        <div className="dp__stat">
          <span className="track__label">notional</span>
          <span className="dp__num num">{money(notional)}</span>
        </div>
        <div className="dp__stat">
          <span className="track__label">lean</span>
          <span className="dp__num num">
            {buys === sells ? (
              <span className="muted">balanced</span>
            ) : (
              <span className={`chip chip--${buys > sells ? "up" : "down"}`}>
                {buys > sells ? `${buys}B / ${sells}S` : `${sells}S / ${buys}B`}
              </span>
            )}
          </span>
        </div>
      </div>

      <table className="levels dp__table">
        <thead>
          <tr>
            <th>time</th>
            <th className="num">price</th>
            <th className="num">size</th>
            <th className="num">premium</th>
            <th>in spread</th>
            <th>venue</th>
          </tr>
        </thead>
        <tbody>
          {prints.map((p, i) => (
            <tr key={i} className={p.canceled ? "dp__row--void" : undefined}>
              <td className="levels__role">{timeOf(p.at)}</td>
              <td className="num levels__price">{price(p.price)}</td>
              <td className="num">{int(p.size)}</td>
              <td className="num levels__dist">{money(p.premium)}</td>
              <td>
                <SpreadBar p={p} />
              </td>
              <td className="levels__role">
                {p.canceled ? (
                  <span className="dp__void">cancelled</span>
                ) : (
                  p.market_center || "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {canceled > 0 && (
        <p className="disclaimer">
          {canceled} cancelled print{canceled === 1 ? "" : "s"} shown struck
          through. A cancellation is not a trade — they are displayed rather
          than filtered so a tape that is mostly cancellations cannot read as
          conviction.
        </p>
      )}

      <p className="disclaimer">
        Side is <em>inferred</em> from where each print sat between the NBBO bid
        and ask — dark pool prints carry no aggressor flag. Treat the lean as a
        read, not a fact.
      </p>
    </div>
  );
}

/** Bid |---o--| Ask. Position is the datum; the label alone loses the degree. */
function SpreadBar({ p }: { p: DarkPoolPrint }) {
  if (p.spread_pos === null || p.spread_pos === undefined) {
    return <span className="muted">no nbbo</span>;
  }
  const pct = p.spread_pos * 100;
  return (
    <span className="dp__spread" title={`bid ${p.nbbo_bid} · ask ${p.nbbo_ask}`}>
      <span className="dp__track">
        <i className={`dp__dot dp__dot--${p.lean}`} style={{ left: `${pct}%` }} />
      </span>
      <em className={`dp__lean dp__lean--${p.lean}`}>{p.lean}</em>
    </span>
  );
}

function timeOf(at: string | null | undefined): string {
  if (!at) return "—";
  const t = Date.parse(at);
  if (!Number.isFinite(t)) return "—";
  return new Date(t).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
