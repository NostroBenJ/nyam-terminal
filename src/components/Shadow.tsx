import { useCallback, useEffect, useState } from "react";
import { api, type ShadowDecision } from "../lib/api";
import { price } from "../lib/format";
import { Empty } from "./Panel";

/**
 * What the recorder makes of the board.
 *
 * DELIBERATELY UNDERSTATED. This is a measurement being taken, not a
 * recommendation being made — nothing in the engine's strategy layer can reach
 * a broker, and there is no ticket and no button here on purpose. Making it
 * look like a signal would defeat what it is for: the shadow exists to measure
 * the strategy independently, and the more it shouts, the more your own trades
 * correlate with it and the less the sample says.
 *
 * THE TALLY IS THE POINT, not the current state. How often a setup appears at
 * all is the first thing worth knowing about this strategy and nothing in the
 * app has ever been able to answer it. Most days the headline will read
 * NO TRADE, and a run of zeros is a finding rather than a fault.
 */
const STATES: Record<string, { label: string; cls: string }> = {
  actionable: { label: "SETUP", cls: "up" },
  blocked: { label: "BLOCKED", cls: "warn" },
  none: { label: "NO TRADE", cls: "flat" },
};

export function Shadow({ ticker }: { ticker: string }) {
  const [d, setD] = useState<ShadowDecision | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setD(await api.shadow(ticker));
      setErr(null);
    } catch {
      setErr("Engine did not answer.");
    }
  }, [ticker]);

  useEffect(() => {
    void load();
    // Matches the engine's own refresh cadence. Polling faster would show the
    // same board twice and cannot change the tally, which is counted from the
    // scheduler rather than from here.
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <Empty>{err}</Empty>;
  if (!d) return <Empty>Reading…</Empty>;

  const state = d.actionable ? "actionable" : d.available ? "blocked" : "none";
  const s = STATES[state];
  const t = d.tally;

  return (
    <div className="shx">
      <div className={`shx__state shx__state--${s.cls}`}>
        <span className="shx__badge">{s.label}</span>
        {d.available && (
          <span className="shx__what">
            {d.playbook} {d.direction}
          </span>
        )}
      </div>

      {d.available ? (
        <>
          <div className="shx__levels num">
            <span className="shx__entry">{price(d.entry)}</span>
            <span className="shx__arrow">→</span>
            <span className="shx__target">{price(d.target)}</span>
            <span className="shx__stop">stop {price(d.stop)}</span>
          </div>
          {d.contract && d.sizing?.ok && (
            <div className="shx__ct">
              <code>{d.contract.symbol}</code>
              <span>
                {d.sizing.contracts}× · ${d.sizing.premium_total}
              </span>
              {/* Both numbers, always. The estimate is what sizing used; the
                  max is what is actually guaranteed, and the gap between them
                  is the thing a single figure would hide. */}
              <span className="shx__risk">
                ${d.sizing.est_loss_at_stop} est at stop · ${d.sizing.max_loss} max
              </span>
            </div>
          )}
          {(d.blocks?.length ?? 0) > 0 && (
            <ul className="shx__blocks">
              {d.blocks!.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
          {(d.warnings?.length ?? 0) > 0 && (
            <ul className="shx__warns">
              {d.warnings!.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="shx__why">{d.reason}</p>
      )}

      <div className="shx__tally">
        <span>
          <b className="num">{t.setups}</b> setup{t.setups === 1 ? "" : "s"} today
        </span>
        <span>
          <b className="num">{t.actionable}</b> clear
        </span>
        {t.blocked > 0 && (
          <span>
            <b className="num">{t.blocked}</b> blocked
          </span>
        )}
        <span className="shx__evals">{t.evaluations} checks</span>
      </div>

      <p className="disclaimer">
        Shadow — records what the board implies and places nothing. No order
        exists anywhere in this app.
      </p>
    </div>
  );
}
