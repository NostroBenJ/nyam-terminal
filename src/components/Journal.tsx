import { useCallback, useEffect, useState } from "react";
import { api, type Journal as JournalData, type JournalRecord } from "../lib/api";
import { money, price, pct } from "../lib/format";
import { Empty } from "./Panel";
import { TrackRecord } from "./TrackRecord";

/**
 * Every call, and why it was made.
 *
 * The track record answers "is this working". The journal answers "why did
 * this one go the way it did" — which is the question that actually changes
 * how you trade. Each row expands to the levels, the per-signal reasoning, and
 * the event risk captured at call time, none of which can be reconstructed
 * afterwards: the chain that produced those levels is gone by the next
 * session.
 *
 * Notes are yours and grading never touches them. "I didn't take this one" and
 * "right read, sized too small" are the annotations that make a losing record
 * legible a month later.
 */

type Filter = "all" | "wins" | "losses" | "pending";

export function Journal({ ticker }: { ticker: string }) {
  const [data, setData] = useState<JournalData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.journal(ticker));
    } catch (e) {
      setError(String(e));
    }
  }, [ticker]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (r: JournalRecord) => {
    const note = draft[r.date] ?? r.note ?? "";
    setSaving(r.date);
    try {
      await api.saveNote(r.ticker, r.date, note);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  };

  if (error && !data) return <Empty>Journal unavailable — {error}</Empty>;
  if (!data) return <Empty>Loading…</Empty>;

  const rows = data.records.filter((r) =>
    filter === "all" ? true
      : filter === "pending" ? !r.outcome
      : filter === "wins" ? r.outcome?.correct === true
      : r.outcome?.correct === false
  );

  return (
    <div className="journal">
      <TrackRecord track={data.stats} />

      <div className="journal__bar">
        <div className="news__toggles">
          {(["all", "wins", "losses", "pending"] as Filter[]).map((f) => (
            <button
              key={f}
              className={`pchart__iv${filter === f ? " pchart__iv--on" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
        <span className="news__count">
          {rows.length} of {data.records.length} calls
        </span>
      </div>

      {data.records.length === 0 ? (
        <Empty>
          No calls recorded yet for {ticker}. One is filed each weekday morning
          and graded after the close — the record starts building from the first
          session this runs.
        </Empty>
      ) : rows.length === 0 ? (
        <Empty>No {filter}.</Empty>
      ) : (
        <ul className="jlist">
          {rows.map((r) => {
            const o = r.outcome;
            const state = !o ? "pending" : o.correct ? "win" : "loss";
            const isOpen = open === r.date;
            return (
              <li key={`${r.ticker}-${r.date}`} className={`jrow jrow--${state}`}>
                <button
                  className="jrow__head"
                  onClick={() => setOpen(isOpen ? null : r.date)}
                  aria-expanded={isOpen}
                >
                  <span className="jrow__date num">{r.date}</span>
                  <span className={`jrow__bias jrow__bias--${dirClass(r.predicted_dir)}`}>
                    {r.bias}
                  </span>
                  <span className="jrow__score num">
                    {r.score > 0 ? "+" : ""}
                    {r.score.toFixed(1)}
                  </span>
                  <span className="jrow__spot num">{price(r.spot)}</span>
                  <span className="jrow__arrow">
                    {r.predicted_dir} → {o ? o.actual_dir : "—"}
                  </span>
                  <span className={`jrow__move num jrow__move--${o && o.move_pct >= 0 ? "up" : "down"}`}>
                    {o ? pct(o.move_pct) : "—"}
                  </span>
                  <span className={`jrow__verdict jrow__verdict--${state}`}>
                    {!o ? "PENDING" : o.correct ? "HIT" : "MISS"}
                  </span>
                  {r.note ? <span className="jrow__hasnote" title={r.note}>✎</span> : null}
                  <span className="jrow__chev">{isOpen ? "▾" : "▸"}</span>
                </button>

                {isOpen && (
                  <div className="jrow__body">
                    {r.context ? (
                      <>
                        <div className="jctx">
                          <Field label="regime" value={r.context.regime} />
                          <Field label="net gex" value={money(r.context.net_gex)} />
                          <Field label="flip" value={price(r.context.gamma_flip)} />
                          <Field label="call wall" value={price(r.context.call_wall)} />
                          <Field label="put wall" value={price(r.context.put_wall)} />
                          <Field label="magnet" value={price(r.context.control_node)} />
                          <Field
                            label="atm iv"
                            value={r.context.atm_iv ? `${(r.context.atm_iv * 100).toFixed(1)}%` : "—"}
                          />
                          <Field label="p/c" value={r.context.put_call_ratio?.toFixed(2)} />
                          <Field label="conviction" value={r.context.conviction} />
                          <Field label="source" value={r.context.provider} />
                        </div>

                        {r.context.summary && <p className="jrow__summary">{r.context.summary}</p>}

                        {r.context.signals && r.context.signals.length > 0 && (
                          <ul className="jsignals">
                            {r.context.signals.map((s, i) => (
                              <li key={i}>
                                <span className={`signal__glyph signal__glyph--${leanClass(s.lean)}`}>
                                  {s.lean > 0 ? "▲" : s.lean < 0 ? "▼" : "■"}
                                </span>
                                <span className="jsignals__name">{s.name}</span>
                                <span className="jsignals__reason">{s.reason}</span>
                              </li>
                            ))}
                          </ul>
                        )}

                        {r.context.news && (
                          <p className="jrow__news">Event risk: {r.context.news}</p>
                        )}
                      </>
                    ) : (
                      <p className="jrow__nocontext">
                        No reasoning captured for this call — it predates context
                        recording. Scored, but not explainable after the fact.
                      </p>
                    )}

                    {o?.note && <p className="jrow__gradenote">Grading note: {o.note}</p>}
                    {o?.rule && (
                      <p className="track__rule">
                        graded <code>{o.rule}</code>
                      </p>
                    )}

                    <div className="jnote">
                      <textarea
                        className="jnote__input"
                        placeholder="Your note — did you take it? how did it feel? what would you do differently?"
                        value={draft[r.date] ?? r.note ?? ""}
                        onChange={(e) =>
                          setDraft((d) => ({ ...d, [r.date]: e.target.value }))
                        }
                        rows={2}
                      />
                      <button
                        className="btn"
                        onClick={() => void save(r)}
                        disabled={saving === r.date}
                      >
                        {saving === r.date ? "…" : "Save"}
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="jctx__f">
      <span className="jctx__k">{label}</span>
      <span className="jctx__v num">{value ?? "—"}</span>
    </div>
  );
}

function dirClass(d: string) {
  return d === "up" ? "up" : d === "down" ? "down" : "flat";
}
function leanClass(l: number) {
  return l > 0 ? "up" : l < 0 ? "down" : "flat";
}
