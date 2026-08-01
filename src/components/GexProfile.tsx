import { useMemo } from "react";
import type { Gex } from "../lib/api";
import { money, price } from "../lib/format";

/**
 * Dealer gamma per strike, drawn as a horizontal bar chart with price on the
 * vertical axis so it aligns with a price chart placed beside it.
 *
 * Conventions this chart commits to:
 *  - Positive dealer gamma extends right in --up, negative extends left in
 *    --down. Direction is also carried by which side of zero the bar is on,
 *    so the red/green pair is never the only cue.
 *  - Observed prices (spot) are SOLID. Modeled levels (flip, walls, magnet)
 *    are DASHED. A computed number must never read as a traded one.
 *  - A null level is omitted and named in the footer, not silently dropped.
 */

const W = 560;
const PAD = { top: 10, right: 68, bottom: 26, left: 62 };

export function GexProfile({ gex, window: win = 0.035 }: { gex: Gex; window?: number }) {
  const { spot, profile } = gex;

  const view = useMemo(() => {
    const lo = spot * (1 - win);
    const hi = spot * (1 + win);
    const rows = profile
      .filter((p) => p.strike >= lo && p.strike <= hi)
      .sort((a, b) => a.strike - b.strike);
    return { rows, lo, hi, hidden: profile.length - rows.length };
  }, [profile, spot, win]);

  const { rows, lo, hi, hidden } = view;

  if (!rows.length) {
    return <p className="empty">No strikes within ±{(win * 100).toFixed(1)}% of spot.</p>;
  }

  const H = Math.max(260, rows.length * 13 + PAD.top + PAD.bottom);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gex))) || 1;
  const zeroX = PAD.left + plotW / 2;
  const xOf = (g: number) => zeroX + (g / maxAbs) * (plotW / 2);
  const yOf = (k: number) => PAD.top + plotH - ((k - lo) / (hi - lo)) * plotH;

  const barH = Math.max(3, (plotH / rows.length) * 0.72);

  /** Modeled levels are dashed; spot is solid. */
  const levels: Array<{ v: number | null; label: string; color: string; dashed: boolean }> = [
    { v: spot, label: `spot ${price(spot)}`, color: "var(--text)", dashed: false },
    { v: gex.gamma_flip, label: `flip ${price(gex.gamma_flip)}`, color: "var(--accent)", dashed: true },
    { v: gex.call_wall, label: `call wall ${price(gex.call_wall)}`, color: "var(--down)", dashed: true },
    { v: gex.put_wall, label: `put wall ${price(gex.put_wall)}`, color: "var(--up)", dashed: true },
  ];

  const missing = levels.filter((l) => l.v === null).map((l) => l.label.split(" ")[0]);
  const inView = levels.filter((l) => l.v !== null && l.v >= lo && l.v <= hi);

  // Strike labels every Nth row so they never collide.
  const labelEvery = Math.max(1, Math.ceil(rows.length / 14));

  return (
    <div className="gexchart">
      <svg viewBox={`0 0 ${W} ${H}`} className="gexchart__svg" role="img"
           aria-label="Dealer gamma exposure by strike">
        {/* zero line */}
        <line x1={zeroX} y1={PAD.top} x2={zeroX} y2={PAD.top + plotH}
              stroke="var(--line)" strokeWidth={1} />

        {/* bars */}
        {rows.map((r) => {
          const y = yOf(r.strike) - barH / 2;
          const x = r.gex >= 0 ? zeroX : xOf(r.gex);
          const w = Math.max(1, Math.abs(xOf(r.gex) - zeroX));
          return (
            <rect key={r.strike} x={x} y={y} width={w} height={barH}
                  fill={r.gex >= 0 ? "var(--up)" : "var(--down)"}
                  opacity={0.85}>
              <title>{`${price(r.strike)}  ${money(r.gex)}`}</title>
            </rect>
          );
        })}

        {/* strike axis */}
        {rows.map((r, i) =>
          i % labelEvery === 0 ? (
            <text key={`k${r.strike}`} x={PAD.left - 8} y={yOf(r.strike) + 3}
                  className="gexchart__tick" textAnchor="end">
              {price(r.strike, 0)}
            </text>
          ) : null
        )}

        {/* level overlays */}
        {inView.map((l) => (
          <g key={l.label}>
            <line x1={PAD.left} y1={yOf(l.v!)} x2={W - PAD.right} y2={yOf(l.v!)}
                  stroke={l.color} strokeWidth={1}
                  strokeDasharray={l.dashed ? "4 3" : undefined} opacity={0.9} />
            <text x={W - PAD.right + 5} y={yOf(l.v!) + 3}
                  className="gexchart__lvl" fill={l.color}>
              {l.label}
            </text>
          </g>
        ))}

        {/* scale footer */}
        <text x={zeroX} y={H - 8} className="gexchart__tick" textAnchor="middle">0</text>
        <text x={PAD.left} y={H - 8} className="gexchart__tick" textAnchor="start">
          {money(-maxAbs)}
        </text>
        <text x={W - PAD.right} y={H - 8} className="gexchart__tick" textAnchor="end">
          {money(maxAbs)}
        </text>
      </svg>

      <p className="gexchart__note">
        Solid = observed price. Dashed = modeled level.
        {hidden > 0 && ` ${hidden} strike${hidden === 1 ? "" : "s"} outside ±${(win * 100).toFixed(1)}% not shown.`}
        {missing.length > 0 && ` No ${missing.join(", ")} in range.`}
      </p>
    </div>
  );
}
