import type { NetFlow as Flow } from "../lib/api";
import { money, pct } from "../lib/format";
import { Empty } from "./Panel";

/**
 * Net option premium through the day: dollars into calls minus dollars into puts.
 *
 * PREMIUM, NOT VOLUME. Volume counts contracts and tells you a strike was
 * busy; premium counts dollars and tells you how much someone was willing to
 * pay to be right. A million lottery-ticket OTM calls and one large ITM block
 * look identical on a volume chart and nothing alike here.
 *
 * The ask-side percentages are the second half of the read. Calls trading at
 * the ask is someone lifting offers; the same calls at the bid is someone
 * being hit. "Calls traded" and "calls were bought" are different claims and
 * the split is what separates them.
 */
export function NetFlow({ flow }: { flow: Flow | null }) {
  if (!flow?.available || !flow.series?.length) {
    return (
      <Empty>
        No net premium ticks yet. This builds through the session and is empty
        before the open.
      </Empty>
    );
  }

  const bullish = flow.net >= 0;

  return (
    <div className="nf">
      <div className="nf__head">
        <div>
          <span className="track__label">net premium</span>
          <div className={`nf__net num nf__net--${bullish ? "up" : "down"}`}>
            {money(flow.net)}
          </div>
        </div>
        <div className="nf__split">
          <Leg label="calls" value={flow.call_premium} askPct={flow.call_ask_pct} up />
          <Leg label="puts" value={flow.put_premium} askPct={flow.put_ask_pct} />
        </div>
      </div>

      <Spark series={flow.series} />

      <p className="disclaimer">
        Cumulative call premium minus put premium across {flow.ticks} one-minute
        ticks. A flow reading, not a forecast — it says where money went, not
        where price goes.
      </p>
    </div>
  );
}

function Leg({
  label, value, askPct, up,
}: { label: string; value: number; askPct: number | null; up?: boolean }) {
  return (
    <div className="nf__leg">
      <span className="track__label">{label}</span>
      <span className={`num nf__legval nf__legval--${up ? "up" : "down"}`}>
        {money(value)}
      </span>
      {askPct !== null && (
        <span className="track__base" title="share of volume that traded at the ask — aggressive buying">
          {pct(askPct - 50, 0).replace("+", "")} ask-lean
        </span>
      )}
    </div>
  );
}

/**
 * Cumulative line. Drawn as an inline SVG rather than pulled in from a chart
 * library — one polyline does not justify a dependency, and the chart bundle
 * already carries the price chart.
 */
function Spark({ series }: { series: { t: string | null; v: number }[] }) {
  const vals = series.map((p) => p.v);
  const min = Math.min(...vals, 0);
  const max = Math.max(...vals, 0);
  const span = max - min || 1;
  const W = 100;
  const H = 34;
  const pts = vals
    .map((v, i) => `${(i / Math.max(vals.length - 1, 1)) * W},${H - ((v - min) / span) * H}`)
    .join(" ");
  // Where zero sits, so crossing it is visible rather than implied.
  const zeroY = H - ((0 - min) / span) * H;
  const last = vals[vals.length - 1];

  return (
    <svg className="nf__spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img" aria-label="cumulative net premium through the session">
      <line x1="0" y1={zeroY} x2={W} y2={zeroY} className="nf__zero" />
      <polyline points={pts} className={`nf__line nf__line--${last >= 0 ? "up" : "down"}`} />
    </svg>
  );
}
