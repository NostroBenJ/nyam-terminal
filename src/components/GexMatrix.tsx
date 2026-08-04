import type { GexMatrix as Grid } from "../lib/api";
import { money, price } from "../lib/format";
import { Empty } from "./Panel";

/**
 * Dealer gamma by strike (rows) and expiry (columns).
 *
 * The point of the grid over the single-expiry profile is that it shows WHERE
 * IN TIME the gamma sits. A wall built entirely out of 0DTE evaporates at the
 * bell; the same wall spread across three expiries is structural and will
 * still be there tomorrow. The aggregate profile cannot tell you which one you
 * are looking at.
 *
 * ONE COLOUR SCALE ACROSS THE WHOLE GRID, from `max_abs`. Scaling per column
 * would make a quiet expiry look exactly as dramatic as a loaded one, which
 * inverts the comparison the grid exists to support.
 */
export function GexMatrix({ grid }: { grid: Grid | null }) {
  if (!grid || !grid.rows?.length) {
    return (
      <Empty>
        No gamma grid — needs at least one expiry with open interest inside the
        strike window.
      </Empty>
    );
  }

  return (
    <div className="mx">
      <table className="mx__table">
        <thead>
          <tr>
            <th className="mx__corner">strike</th>
            {grid.expiries.map((e) => (
              <th key={e.label} className="mx__exp" title={e.label}>
                {e.dte === 0 ? "0DTE" : `${e.dte}d`}
              </th>
            ))}
            <th className="mx__total">net</th>
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((r) => (
            <tr key={r.strike} className={r.at_spot ? "mx__row--spot" : undefined}>
              <td className="mx__strike num">
                {price(r.strike, 0)}
                {r.at_spot && <i className="mx__here" title="spot sits here" />}
              </td>
              {r.cells.map((c, i) => (
                <td key={i} className="mx__cell" title={c === null ? "no open interest" : money(c)}>
                  <span className="mx__box" style={cellStyle(c, grid.max_abs)} />
                </td>
              ))}
              <td className={`mx__total num ${r.total >= 0 ? "up" : "down"}`}>
                {money(r.total)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mx__legend">
        <span className="mx__key mx__key--neg" /> short gamma
        <span className="mx__key mx__key--zero" /> none
        <span className="mx__key mx__key--pos" /> long gamma
        <em>scale ±{money(grid.max_abs)} across the whole grid</em>
      </div>

      <p className="disclaimer">
        Columns are expiries, so a wall you can see stacked in one column is a
        0DTE artefact that dies at the bell. The same magnitude spread across
        several columns is structural.
      </p>
    </div>
  );
}

/**
 * Colour by signed magnitude against the grid-wide maximum.
 *
 * Uses a square-root ramp, not linear: gamma is dominated by a handful of
 * strikes, so on a linear scale everything except the control node renders as
 * near-empty and the structure the grid exists to show disappears.
 */
function cellStyle(v: number | null, maxAbs: number): React.CSSProperties {
  if (v === null || !maxAbs) return { opacity: 0.06 };
  const mag = Math.sqrt(Math.min(Math.abs(v) / maxAbs, 1));
  return {
    background: v >= 0 ? "var(--up)" : "var(--down)",
    opacity: 0.1 + mag * 0.9,
  };
}
