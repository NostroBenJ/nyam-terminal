import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TriggerVerdict } from "../lib/api";
import { price as fmtPrice } from "../lib/format";
import { Empty } from "./Panel";

/**
 * What the board says about an entry trigger at a given price.
 *
 * The dashboard answers WHERE — walls, flip, magnet, dealer positioning. An
 * external entry model (CISD° on TradingView) answers WHEN. Neither knows
 * about the other, and the join happens in your head at 09:31 while a candle
 * closes. This does the join.
 *
 * IT HAS NO OPINION OF ITS OWN. Every verdict is read off the engine's trade
 * plan, which already encodes the house rule — in positive gamma dealers
 * dampen so walls hold, in negative gamma they amplify so the same levels
 * become accelerants. A long at the put wall is a different trade in each.
 *
 * THE DISAGREEMENT IS THE POINT. Two systems agreeing is weak evidence when
 * they share inputs, and these do — both read prior-day levels and liquidity
 * sweeps. A conflict is the case worth showing loudest.
 */
const VERDICTS: Record<string, { label: string; cls: string }> = {
  aligned:  { label: "ALIGNED",   cls: "up" },
  conflict: { label: "CONFLICT",  cls: "down" },
  mixed:    { label: "SPLIT",     cls: "warn" },
  neutral:  { label: "NEUTRAL",   cls: "flat" },
  no_level: { label: "OPEN SPACE", cls: "flat" },
};

export function TriggerCheck({ ticker, spot }: { ticker: string; spot: number }) {
  const [raw, setRaw] = useState("");
  const [dir, setDir] = useState<"long" | "short">("long");
  const [v, setV] = useState<TriggerVerdict | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const run = useCallback(
    async (p: number, d: "long" | "short") => {
      const mine = ++seq.current;
      setBusy(true);
      try {
        const out = await api.trigger(ticker, p, d);
        // Drop a slow response that a newer request has already overtaken —
        // otherwise a stale verdict lands on top of a current one.
        if (mine === seq.current) setV(out);
      } catch {
        if (mine === seq.current) {
          setV({ available: false, note: "Engine did not answer." });
        }
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    },
    [ticker]
  );

  // A blank box means "read spot", so the panel is useful with no typing.
  const parsed = raw.trim() === "" ? spot : Number(raw);
  const valid = Number.isFinite(parsed) && parsed > 0;

  useEffect(() => {
    if (valid) void run(parsed, dir);
  }, [parsed, dir, valid, run]);

  const vd = v?.verdict ? VERDICTS[v.verdict] : null;

  return (
    <div className="trig">
      <div className="trig__in">
        <input
          className="trig__price num"
          value={raw}
          inputMode="decimal"
          placeholder={fmtPrice(spot)}
          onChange={(e) => setRaw(e.target.value)}
          aria-label="Trigger price"
        />
        <div className="trig__dirs">
          <button
            className={`trig__dir${dir === "long" ? " trig__dir--on trig__dir--long" : ""}`}
            onClick={() => setDir("long")}
          >
            LONG
          </button>
          <button
            className={`trig__dir${dir === "short" ? " trig__dir--on trig__dir--short" : ""}`}
            onClick={() => setDir("short")}
          >
            SHORT
          </button>
        </div>
      </div>

      {!valid ? (
        <Empty>Enter a trigger price.</Empty>
      ) : !v ? (
        <Empty>{busy ? "Reading the board…" : "—"}</Empty>
      ) : !v.available ? (
        <Empty>{v.note || "No read available."}</Empty>
      ) : (
        <>
          <div className={`trig__verdict trig__verdict--${vd?.cls ?? "flat"}`}>
            <span className="trig__badge">{vd?.label ?? v.verdict}</span>
            <span className="trig__head">{v.headline}</span>
          </div>

          {v.note && <p className="trig__why">{v.note}</p>}

          {(v.near?.length ?? 0) > 0 && (
            <table className="trig__lv">
              <tbody>
                {v.near!.map((e) => (
                  <tr key={`${e.level}-${e.label}`}>
                    <td className="trig__lvp num">{fmtPrice(e.level)}</td>
                    <td className="trig__lvl">{e.label}</td>
                    <td
                      className={`trig__act trig__act--${
                        e.supports === null
                          ? "unknown"
                          : e.supports === 0
                          ? "flat"
                          : (e.supports > 0) === (v.direction === "long")
                          ? "up"
                          : "down"
                      }`}
                    >
                      {e.action}
                    </td>
                    <td className="trig__lvd num">
                      {e.distance === 0 ? "on" : `${fmtPrice(e.distance)} away`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {(v.confluence?.length ?? 0) > 0 && (
            <div className="trig__conf">
              {v.confluence!.map((c) => (
                <span key={c.label} className="chip chip--warn">
                  {c.label}
                </span>
              ))}
            </div>
          )}

          <div className="trig__tg">
            {v.targets?.next_level ? (
              <span>
                next level{" "}
                <b className="num">{fmtPrice(v.targets.next_level.level)}</b>{" "}
                {v.targets.next_level.label}
              </span>
            ) : (
              <span>no further board level this way</span>
            )}
            {v.targets?.em_edge != null && (
              <span title={v.targets.em_note}>
                · 1σ edge <b className="num">{fmtPrice(v.targets.em_edge)}</b>
              </span>
            )}
          </div>

          <p className="disclaimer">
            Read off the board's own trade plan for the {v.regime} gamma regime,
            within {fmtPrice(v.band ?? 0)} of {fmtPrice(v.price ?? 0)}. It says
            what your levels imply about this trigger — not whether to take it.
          </p>
        </>
      )}
    </div>
  );
}
