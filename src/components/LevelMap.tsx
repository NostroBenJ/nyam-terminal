import type { Confluence, ExpiryConfluence, LevelMapRow } from "../lib/api";
import { price } from "../lib/format";

/**
 * The day's reference prices, ordered high to low as they sit on a chart.
 * The engine already assigns each one a role and a class; this renders that
 * ordering rather than re-deriving it.
 *
 * The two confluence lists below it were computed on every build, shipped on
 * every payload, and drawn nowhere — the only thing reading them was chat.py,
 * which feeds them to the model, so they reached you only if the brief
 * happened to mention them in prose. levels.py calls these "the levels that
 * actually matter at the open". They matter more than most of what IS drawn.
 */
export function LevelMap({
  rows,
  spot,
  confluences = [],
  expiryConfluence = [],
}: {
  rows: LevelMapRow[];
  spot: number;
  confluences?: Confluence[];
  expiryConfluence?: ExpiryConfluence[];
}) {
  if (!rows.length) return <p className="empty">No levels resolved for this session.</p>;

  const sorted = [...rows].sort((a, b) => b.price - a.price);
  // THREE, not two. derived.py's rule of thumb is "needs all 5 expirations",
  // and at >= 2 the live board produced six rows — half of them 2/5, two of
  // them competing gamma flips five points apart — under a table that only
  // has five rows itself. A weak agreement listed beside a strong one reads
  // as equally load-bearing, which is the opposite of what this says.
  const confirmed = expiryConfluence
    .filter((c) => c.count >= 3)
    .slice(0, 5);

  return (
    <>
    <table className="levels">
      <tbody>
        {sorted.map((r) => {
          const dist = ((r.price - spot) / spot) * 100;
          const isSpot = Math.abs(r.price - spot) < 1e-9;
          return (
            <tr key={`${r.price}-${r.role}`} className={isSpot ? "levels__row--spot" : undefined}>
              <td className={`levels__price num levels__price--${r.cls}`}>{price(r.price)}</td>
              {/* Two levels on one price is the highest-conviction reaction
                  point on the board. The engine used to resolve the tie by
                  dropping a row, which lost the flip whenever it landed on
                  the magnet or on spot. */}
              <td className="levels__role">
                {r.role}
                {r.confluence && (
                  <span className="chip chip--warn levels__conf">confluence</span>
                )}
              </td>
              <td className={`levels__tag levels__tag--${r.cls}`}>{r.tag}</td>
              <td className="levels__dist num">
                {isSpot ? "—" : `${dist > 0 ? "+" : "−"}${Math.abs(dist).toFixed(2)}%`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>

    {confluences.length > 0 && (
      <div className="conf">
        <div className="conf__head">STACKED — a session level on a gamma level</div>
        {confluences.map((c) => (
          <div className="conf__row" key={`${c.price}-${c.label}`}>
            <span className="conf__price num">{price(c.price)}</span>
            <span className="conf__label">{c.label}</span>
            <span className="conf__dist num">
              {`${c.price > spot ? "+" : "−"}${(Math.abs(c.price - spot) / spot * 100).toFixed(2)}%`}
            </span>
          </div>
        ))}
      </div>
    )}

    {confirmed.length > 0 && (
      <div className="conf">
        <div className="conf__head">CONFIRMED ACROSS EXPIRIES</div>
        {confirmed.map((c) => (
          <div className="conf__row" key={`${c.type}-${c.price}`}>
            <span className="conf__price num">{price(c.price)}</span>
            <span className="conf__label">{c.type}</span>
            <span className="conf__dist num">
              {c.count}/{c.total}
              {c.full && <span className="chip chip--warn levels__conf">all</span>}
            </span>
          </div>
        ))}
      </div>
    )}
    </>
  );
}
