import type { Bias } from "../lib/api";

/**
 * The weighted lean and, more importantly, the reasoning under it.
 *
 * The label is deliberately not the whole panel. A bias with no visible
 * per-signal breakdown invites being read as a prediction; showing each
 * signal's direction, weight and reason keeps it what it is — a weighted
 * combination you can disagree with.
 */

function leanClass(lean: number): string {
  return lean > 0 ? "up" : lean < 0 ? "down" : "flat";
}

function leanGlyph(lean: number): string {
  return lean > 0 ? "▲" : lean < 0 ? "▼" : "■";
}

export function BiasPanel({ bias }: { bias: Bias }) {
  const maxWeight = Math.max(...bias.signals.map((s) => s.weight), 1);
  const dir = bias.score > 0 ? "up" : bias.score < 0 ? "down" : "flat";

  return (
    <div className="bias">
      <div className="bias__head">
        <span className={`bias__label bias__label--${dir}`}>{bias.label || "—"}</span>
        <span className="bias__score num">
          {bias.score > 0 ? "+" : ""}
          {bias.score.toFixed(1)}
        </span>
        <span className={`chip chip--${bias.conviction === "reduced" ? "warn" : "quiet"}`}>
          {bias.conviction}
        </span>
      </div>

      <p className="bias__summary">{bias.summary}</p>

      <ul className="signals">
        {bias.signals.map((s) => (
          <li key={s.name} className="signal">
            <div className="signal__head">
              <span className={`signal__glyph signal__glyph--${leanClass(s.lean)}`}>
                {leanGlyph(s.lean)}
              </span>
              <span className="signal__name">{s.name}</span>
              <span className="signal__weight num" title={`weight ${s.weight}`}>
                {s.weight.toFixed(2)}
              </span>
            </div>
            <div className="signal__bar" aria-hidden="true">
              <i
                className={`signal__fill signal__fill--${leanClass(s.lean)}`}
                style={{ width: `${(s.weight / maxWeight) * 100}%` }}
              />
            </div>
            <p className="signal__reason">{s.reason}</p>
          </li>
        ))}
      </ul>

      {bias.scenarios.length > 0 && (
        <div className="scenarios">
          <h3 className="panel-title">If / then</h3>
          <ul>
            {bias.scenarios.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="disclaimer">
        A weighted lean from positioning data, with its reasoning. Not a prediction.
      </p>
    </div>
  );
}
