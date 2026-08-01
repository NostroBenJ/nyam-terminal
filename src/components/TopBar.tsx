import type { Snapshot } from "../lib/api";
import { money, price, pct, ageLabel, freshness, type Freshness } from "../lib/format";
import { Flash } from "./Flash";

/**
 * Ticker, spot, and the top-line positioning read.
 *
 * The provider chip is not cosmetic: a number sourced from the free delayed
 * feed and one from a paid real-time feed are different claims, and the UI
 * has to say which it is showing.
 */
export function TopBar({
  snap,
  ageSec,
  busy,
  onTicker,
  onRefresh,
}: {
  snap: Snapshot;
  ageSec: number | null;
  busy: boolean;
  onTicker: (t: string) => void;
  onRefresh: () => void;
}) {
  const g = snap.gex;
  const fresh: Freshness = freshness(ageSec);
  const source = snap.mock ? "MOCK" : snap.provider.toUpperCase();

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <span className="topbar__mark">NYAM</span>
        <span className="topbar__sub">Terminal</span>
      </div>

      <select
        className="topbar__ticker"
        value={snap.ticker}
        onChange={(e) => onTicker(e.target.value)}
        disabled={busy}
        aria-label="Underlying"
      >
        {snap.tickers.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <div className="stat">
        <span className="stat__label">spot</span>
        <span className="stat__value num">
          <Flash value={g.spot} format={(v) => price(v)} />
        </span>
      </div>
      <div className="stat">
        <span className="stat__label">net gex</span>
        <span className={`stat__value num stat__value--${g.net_gex >= 0 ? "up" : "down"}`}>
          <Flash value={g.net_gex} format={(v) => money(v)} showArrow={false} />
        </span>
      </div>
      <div className="stat">
        <span className="stat__label">regime</span>
        <span className={`chip chip--${g.regime === "positive" ? "up" : "down"}`}>
          {g.regime === "positive" ? "POSITIVE γ" : "NEGATIVE γ"}
        </span>
      </div>
      <Stat
        label="flip"
        value={g.gamma_flip === null ? "none in range" : price(g.gamma_flip)}
        cls={g.gamma_flip === null ? "muted" : undefined}
      />
      <Stat label="atm iv" value={`${(g.atm_iv * 100).toFixed(1)}`} />
      <Stat
        label="1σ"
        value={`${price(snap.expected_move.low)}–${price(snap.expected_move.high)}`}
        sub={pct(snap.expected_move.pct)}
      />

      <div className="topbar__spacer" />

      <span className={`chip chip--${snap.mock ? "warn" : "quiet"}`} title={
        snap.mock ? "Synthetic data. Nothing here reflects the live market."
                  : `Live provider: ${snap.provider}`
      }>
        {source}
      </span>

      <span className={`age age--${fresh}`}>
        <i className="age__dot" />
        {fresh === "unknown" ? "age unknown" : ageLabel(ageSec)}
      </span>

      <button className="btn" onClick={onRefresh} disabled={busy}>
        {busy ? "…" : "Refresh"}
      </button>
    </header>
  );
}

function Stat({
  label,
  value,
  sub,
  cls,
}: {
  label: string;
  value: string;
  sub?: string;
  cls?: string;
}) {
  return (
    <div className="stat">
      <span className="stat__label">{label}</span>
      <span className={`stat__value num${cls ? ` stat__value--${cls}` : ""}`}>
        {value}
        {sub && <em className="stat__sub">{sub}</em>}
      </span>
    </div>
  );
}
