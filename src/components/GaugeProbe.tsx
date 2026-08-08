import type { Gex } from "../lib/api";
import { Gauge } from "./Gauge";

/**
 * Every degradation path the gauge claims to handle, rendered at once.
 *
 * The gauge implies precision — a needle at a position is a strong visual
 * claim — so each case where that precision is not real has to degrade to
 * something truthful. Those paths were written from reasoning and had never
 * been executed, which is the same state the null that blanked the whole board
 * was in. Reasoning is not evidence.
 *
 * Open with `?gauge=1`. Costs nothing when the flag is absent, which is always
 * in normal use.
 */
export function gaugeProbeRequested(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("gauge") === "1";
  } catch {
    return false;
  }
}

/** A full Gex with only the fields the gauge reads varied per case. */
function gex(over: Partial<Gex>): Gex {
  return {
    spot: 773.38,
    net_gex: 3_750_000_000,
    regime: "positive",
    gamma_flip: 764.52,
    control_node: 775,
    atm_iv: 0.072,
    call_wall: 775,
    put_wall: 763,
    profile: [],
    put_call_ratio: 1.4,
    call_oi: 100,
    put_oi: 140,
    call_oi_change: 0,
    put_oi_change: 0,
    ...over,
  } as Gex;
}

const CASES: { name: string; expect: string; gex: Gex }[] = [
  {
    name: "baseline — both walls, price inside",
    expect: "needle inside the band, hatching up to the flip",
    gex: gex({}),
  },
  {
    name: "no walls at all",
    expect: "NO GAUGE — a sentence saying there is nothing to place price between",
    gex: gex({ call_wall: null, put_wall: null }),
  },
  {
    name: "put wall only (no call wall)",
    expect: "band open ABOVE, right edge labelled open, no fabricated ceiling",
    gex: gex({ call_wall: null }),
  },
  {
    name: "call wall only (no put wall)",
    expect: "band open BELOW, left edge labelled open",
    gex: gex({ put_wall: null }),
  },
  {
    name: "walls crossed (put above call)",
    expect: "REFUSES to draw, points at the level map",
    gex: gex({ put_wall: 790, call_wall: 760 }),
  },
  {
    name: "walls equal",
    expect: "REFUSES to draw — zero width band",
    gex: gex({ put_wall: 770, call_wall: 770 }),
  },
  {
    name: "no gamma flip",
    expect: "NO hatching, and the legend says the regime boundary is unknown",
    gex: gex({ gamma_flip: null }),
  },
  {
    name: "price ABOVE the call wall",
    expect: "needle pinned right AND a sentence saying it is above, not on, the wall",
    gex: gex({ spot: 782.5 }),
  },
  {
    name: "price BELOW the put wall",
    expect: "needle pinned left AND a sentence saying below",
    gex: gex({ spot: 755.2 }),
  },
  {
    name: "price exactly on the call wall",
    expect: "needle at the edge and NO outside-sentence — this is a real touch",
    gex: gex({ spot: 775 }),
  },
  {
    name: "flip below the put wall (outside the band)",
    expect: "hatching clamped to zero width, not negative or overflowing",
    gex: gex({ gamma_flip: 740 }),
  },
  {
    name: "flip above the call wall",
    expect: "hatching clamped to full width, not overflowing the scale",
    gex: gex({ gamma_flip: 800 }),
  },
];

export function GaugeProbe() {
  return (
    <div style={{ padding: "22px 26px", overflow: "auto", height: "100%" }}>
      <h1 style={{ font: "600 18px/1.2 var(--sans)", margin: "0 0 4px" }}>
        Gauge degradation paths
      </h1>
      <p style={{ font: "400 12.5px/1.5 var(--sans)", color: "var(--muted)", margin: "0 0 22px", maxWidth: "68ch" }}>
        Every case the component claims to handle. Each block states what it
        should do; compare against what it does. A gauge that draws a confident
        needle on a case it cannot actually measure is worse than one that
        refuses.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        {CASES.map((c) => (
          <section key={c.name} style={{ border: "1px solid var(--line)", padding: "12px 14px 16px" }}>
            <div style={{ font: "600 11px/1.3 var(--mono)", letterSpacing: ".08em", textTransform: "uppercase" }}>
              {c.name}
            </div>
            <div style={{ font: "400 11.5px/1.45 var(--sans)", color: "var(--muted)", margin: "4px 0 8px" }}>
              expect: {c.expect}
            </div>
            <div style={{ font: "400 10px/1.4 var(--mono)", color: "var(--dim)", marginBottom: 4 }}>
              spot {String(c.gex.spot)} · put {String(c.gex.put_wall)} · call{" "}
              {String(c.gex.call_wall)} · flip {String(c.gex.gamma_flip)}
            </div>
            <Gauge gex={c.gex} />
          </section>
        ))}
      </div>
    </div>
  );
}
