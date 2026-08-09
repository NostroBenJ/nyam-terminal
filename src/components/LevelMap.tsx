import type { LevelMapRow } from "../lib/api";
import { price } from "../lib/format";

/**
 * The day's reference prices, ordered high to low as they sit on a chart.
 * The engine already assigns each one a role and a class; this renders that
 * ordering rather than re-deriving it.
 */
export function LevelMap({ rows, spot }: { rows: LevelMapRow[]; spot: number }) {
  if (!rows.length) return <p className="empty">No levels resolved for this session.</p>;

  const sorted = [...rows].sort((a, b) => b.price - a.price);

  return (
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
                {r.confluence && <span className="chip chip--warn">confluence</span>}
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
  );
}
