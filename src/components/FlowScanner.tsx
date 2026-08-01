import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Flow, type FlowRow } from "../lib/api";
import { money, price, int } from "../lib/format";

/**
 * Options order flow scanner — the OptionsFlow centrepiece.
 *
 * Columns and filters follow that decomposition: premium, DTE and trade type
 * above an always-visible table. Sorting and filtering happen client-side
 * because the whole set is already here and a round-trip per sort would make
 * the table feel dead.
 *
 * Until a UW key exists this renders SYNTHETIC rows so the table, filters and
 * sort are finished and reviewable now. Every such row is marked and the panel
 * says so at the top — the point is a working scanner, not a populated-looking
 * one. Real prints and fake prints must never be confusable.
 */

type SortKey = "at" | "premium" | "size" | "dte" | "vol_oi";

const QUICK_PREMIUM = [0, 25_000, 100_000, 500_000, 1_000_000];

export function FlowScanner({ ticker }: { ticker: string }) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [minPremium, setMinPremium] = useState(0);
  const [maxDte, setMaxDte] = useState<number | null>(null);
  const [types, setTypes] = useState<Set<string>>(new Set());
  const [rights, setRights] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortKey>("premium");

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setFlow(await api.flow(ticker));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [ticker]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(() => {
    if (!flow) return [];
    let r = flow.rows.filter((x) => x.premium >= minPremium);
    if (maxDte !== null) r = r.filter((x) => x.dte !== null && x.dte <= maxDte);
    if (types.size) r = r.filter((x) => types.has(x.trade_type));
    if (rights.size) r = r.filter((x) => rights.has(x.right));
    const dir = sort === "at" ? -1 : -1;
    return [...r].sort((a, b) => {
      const av = sort === "at" ? a.at : (a[sort] ?? 0);
      const bv = sort === "at" ? b.at : (b[sort] ?? 0);
      return av === bv ? 0 : (av < bv ? 1 : -1) * (dir === -1 ? 1 : -1);
    });
  }, [flow, minPremium, maxDte, types, rights, sort]);

  const toggle = (set: Set<string>, v: string, fn: (s: Set<string>) => void) => {
    const next = new Set(set);
    next.has(v) ? next.delete(v) : next.add(v);
    fn(next);
  };

  if (error && !flow) return <p className="empty">Flow unavailable — {error}</p>;
  if (!flow) return <p className="empty">Loading flow…</p>;

  const totalPrem = rows.reduce((s, r) => s + r.premium, 0);
  const callPrem = rows.filter((r) => r.right === "C").reduce((s, r) => s + r.premium, 0);
  const putPrem = totalPrem - callPrem;

  return (
    <div className="flow">
      {/* The scaffold has to announce itself louder than it displays. */}
      {!flow.available && (
        <div className={`banner banner--${flow.source === "mock" ? "warn" : "error"} flow__banner`}>
          {flow.source === "mock" ? "SYNTHETIC FLOW — " : "FLOW UNAVAILABLE — "}
          {flow.note}
        </div>
      )}

      <div className="flow__filters">
        <div className="flow__group">
          <span className="flow__glabel">min premium</span>
          {QUICK_PREMIUM.map((p) => (
            <button
              key={p}
              className={`pchart__iv${minPremium === p ? " pchart__iv--on" : ""}`}
              onClick={() => setMinPremium(p)}
            >
              {p === 0 ? "any" : money(p).replace("+$", "$")}
            </button>
          ))}
        </div>

        <div className="flow__group">
          <span className="flow__glabel">dte</span>
          {[
            { l: "0DTE", v: 0 },
            { l: "≤7", v: 7 },
            { l: "≤30", v: 30 },
            { l: "any", v: null },
          ].map((d) => (
            <button
              key={d.l}
              className={`pchart__iv${maxDte === d.v ? " pchart__iv--on" : ""}`}
              onClick={() => setMaxDte(d.v)}
            >
              {d.l}
            </button>
          ))}
        </div>

        <div className="flow__group">
          <span className="flow__glabel">type</span>
          {["SWEEP", "BLOCK", "SPLIT"].map((t) => (
            <button
              key={t}
              className={`pchart__iv${types.has(t) ? " pchart__iv--on" : ""}`}
              onClick={() => toggle(types, t, setTypes)}
            >
              {t.toLowerCase()}
            </button>
          ))}
        </div>

        <div className="flow__group">
          <span className="flow__glabel">side</span>
          {[
            { l: "calls", v: "C" },
            { l: "puts", v: "P" },
          ].map((r) => (
            <button
              key={r.v}
              className={`pchart__iv${rights.has(r.v) ? " pchart__iv--on" : ""}`}
              onClick={() => toggle(rights, r.v, setRights)}
            >
              {r.l}
            </button>
          ))}
        </div>

        <button className="btn" onClick={() => void load()} disabled={busy}>
          {busy ? "…" : "Refresh"}
        </button>
      </div>

      <div className="flow__summary">
        <span>{rows.length} of {flow.rows.length} prints</span>
        <span className="num">total {money(totalPrem)}</span>
        <span className="num flow__calls">calls {money(callPrem)}</span>
        <span className="num flow__puts">puts {money(putPrem)}</span>
      </div>

      {rows.length === 0 ? (
        <p className="empty">No prints match these filters.</p>
      ) : (
        <div className="flow__tablewrap">
          <table className="flowtable">
            <thead>
              <tr>
                <Th k="at" sort={sort} set={setSort}>time</Th>
                <th>c/p</th>
                <th className="num">strike</th>
                <Th k="dte" sort={sort} set={setSort} num>dte</Th>
                <Th k="size" sort={sort} set={setSort} num>size</Th>
                <th className="num">price</th>
                <Th k="premium" sort={sort} set={setSort} num>premium</Th>
                <th>type</th>
                <Th k="vol_oi" sort={sort} set={setSort} num>vol/oi</Th>
                <th>sentiment</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((r, i) => (
                <Row key={`${r.at}-${i}`} r={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({
  k, sort, set, num, children,
}: {
  k: SortKey; sort: SortKey; set: (s: SortKey) => void; num?: boolean; children: React.ReactNode;
}) {
  return (
    <th className={`${num ? "num " : ""}flowtable__sortable${sort === k ? " flowtable__sortable--on" : ""}`}
        onClick={() => set(k)}>
      {children}{sort === k ? " ▾" : ""}
    </th>
  );
}

function Row({ r }: { r: FlowRow }) {
  const t = r.at ? r.at.slice(11, 19) : "—";
  const sent = r.sentiment.toLowerCase();
  return (
    <tr className={r.mock ? "flowtable__row--mock" : undefined}>
      <td className="num flowtable__time">{t}</td>
      <td className={`flowtable__right flowtable__right--${r.right === "C" ? "up" : "down"}`}>
        {r.right}
      </td>
      <td className="num">{price(r.strike)}</td>
      <td className="num">{r.dte === null ? "—" : r.dte}</td>
      <td className="num">{int(r.size)}</td>
      <td className="num">{price(r.price)}</td>
      <td className="num flowtable__prem">{money(r.premium)}</td>
      <td className="flowtable__type">{r.trade_type}</td>
      <td className="num">{r.vol_oi ?? "—"}</td>
      <td className={`flowtable__sent flowtable__sent--${sent}`}>{r.sentiment}</td>
    </tr>
  );
}
