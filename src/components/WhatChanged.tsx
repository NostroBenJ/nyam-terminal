import { useCallback, useEffect, useState } from "react";
import { api, type Changes } from "../lib/api";
import { money, price } from "../lib/format";
import { Empty } from "./Panel";

/**
 * What moved since the prior session's close.
 *
 * Every other surface here is a snapshot. This is the only one that answers
 * the question you actually have at 9:30 — a put wall at 763 means one thing
 * on its own and quite another if it was 758 yesterday and five points of
 * support just appeared underneath you.
 *
 * ORDERED BY WHAT CHANGES A DECISION, not by size of move. A regime flip leads
 * however small the number, because it inverts how everything else is traded;
 * ATM IV sorts last however large its percentage, because a vol tick is not a
 * decision.
 */
export function WhatChanged({ ticker }: { ticker: string }) {
  const [d, setD] = useState<Changes | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setD(await api.changed(ticker));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [ticker]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !d) return <Empty>Comparison unavailable — {error}</Empty>;
  if (!d) return <Empty>…</Empty>;

  // "Nothing moved" and "we have nothing to compare against" look identical in
  // an empty list and mean opposite things, so they are different renderings.
  if (!d.available) return <Empty>{d.note}</Empty>;

  return (
    <div className="chg">
      <div className="chg__base">
        vs {d.baseline_date}
        {typeof d.age_days === "number" && d.age_days > 1 &&
          ` · ${d.age_days} sessions back`}
        {d.stale_baseline && (
          <span className="chg__stale">
            {" "}— baseline is old, this is not "since yesterday"
          </span>
        )}
      </div>

      {d.changes.length === 0 ? (
        <p className="chg__none">{d.note}</p>
      ) : (
        <ul className="chg__list">
          {d.changes.map((c) => (
            <li key={c.key} className={`chg__row chg__row--${c.kind}`}>
              <div className="chg__k">{c.label}</div>
              <div className="chg__v num">
                {!c.known ? (
                  <span className="chg__unknown">
                    {c.before === null ? "not recorded before" : "gone"}
                  </span>
                ) : (
                  <>
                    <span className="chg__from">{fmt(c.key, c.before)}</span>
                    <i className="chg__arrow">→</i>
                    <span className="chg__to">{fmt(c.key, c.after)}</span>
                  </>
                )}
              </div>
              <div className={`chg__d num chg__d--${dir(c.move)}`}>
                {c.move === null
                  ? "—"
                  : c.kind === "size"
                    ? `${c.pct! > 0 ? "+" : "−"}${Math.abs(c.pct!)}%`
                    : `${c.move > 0 ? "+" : "−"}${Math.abs(c.move)}`}
              </div>
              {c.note && <p className="chg__note">{c.note}</p>}
            </li>
          ))}
        </ul>
      )}

      <p className="disclaimer">
        Compared against the recorder's snapshot from the last trading day, not
        against whatever was last on screen — so it means "overnight" even after
        a restart.
      </p>
    </div>
  );
}

function dir(move: number | null): string {
  if (move === null) return "flat";
  return move > 0 ? "up" : move < 0 ? "down" : "flat";
}

/** Levels read as prices, sizes as money, everything else as-is. */
function fmt(key: string, v: number | string | null): string {
  if (v === null) return "—";
  if (typeof v === "string") return v;
  if (key === "net_gex") return money(v);
  if (key === "atm_iv" || key === "put_call_ratio") return String(v);
  return price(v);
}
