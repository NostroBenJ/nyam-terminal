import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { api, INTERVALS, type Gex, type Interval } from "../lib/api";
import { tokens } from "../lib/tokens";
import { price } from "../lib/format";
import { loadUi, saveUi } from "../lib/persist";

/**
 * Price candles with our own dealer-gamma levels drawn on them.
 *
 * This overlay is the reason this chart exists rather than an embedded
 * TradingView widget: the flip, the walls and the magnet are computed by our
 * engine and verified by `verify_gex.py`. No off-the-shelf chart can draw them.
 *
 * The levels are MODELED, the candles are OBSERVED, and the two must never be
 * mistakable for one another — so every computed level is dashed and every
 * observed price is solid. A gamma flip that looked like a traded price would
 * be a genuinely dangerous thing to put in front of someone sizing a position.
 */

/** Levels are rebuilt on change rather than diffed — there are at most five. */
type LevelSpec = {
  key: string;
  price: number | null;
  title: string;
  color: string;
  dashed: boolean;
};

export function PriceChart({ gex, ticker }: { gex: Gex; ticker: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  /** Level prices the autoscale provider must keep in frame. */
  const levelPrices = useRef<number[]>([]);

  const [interval, setIntervalState] = useState<Interval>(
    () => (loadUi().interval as Interval) || "5m"
  );
  const setInterval = (iv: Interval) => {
    setIntervalState(iv);
    saveUi({ interval: iv });
  };
  const [meta, setMeta] = useState<{ source: string; note: string; n: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // --- create once -------------------------------------------------------
  useEffect(() => {
    if (!box.current) return;
    const t = tokens();

    const c = createChart(box.current, {
      layout: {
        background: { color: "transparent" },
        textColor: t["--muted"],
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: t["--line"] },
        horzLines: { color: t["--line"] },
      },
      rightPriceScale: { borderColor: t["--line"] },
      timeScale: { borderColor: t["--line"], timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: t["--dim"], labelBackgroundColor: t["--panel-2"] },
        horzLine: { color: t["--dim"], labelBackgroundColor: t["--panel-2"] },
      },
      autoSize: true,
    });

    const s = c.addSeries(CandlestickSeries, {
      upColor: t["--up"],
      downColor: t["--down"],
      borderUpColor: t["--up"],
      borderDownColor: t["--down"],
      wickUpColor: t["--up"],
      wickDownColor: t["--down"],
      // Keep every level in view, not just the candle range.
      //
      // Autoscaling to the bars alone pushes a distant wall off-screen, and a
      // wall you can't see is the one you forget is there. The whole point of
      // this chart is price *relative to* the gamma structure, so the structure
      // sets the frame. Reads the ref rather than closing over state so it
      // stays correct across snapshot updates without re-creating the series.
      autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => {
        const base = orig();
        const lv = levelPrices.current;
        if (!lv.length) return base;
        // priceRange is null before the series has data — then the levels are
        // the only thing to frame, which is still better than an empty scale.
        const pr = base?.priceRange;
        return {
          ...(base ?? {}),
          priceRange: {
            minValue: Math.min(pr ? pr.minValue : Infinity, ...lv),
            maxValue: Math.max(pr ? pr.maxValue : -Infinity, ...lv),
          },
        };
      },
    });

    chart.current = c;
    series.current = s;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
  }, []);

  // --- load bars ---------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .bars(ticker, interval, interval === "1m" ? 2 : 5)
      .then((res) => {
        if (cancelled || !series.current) return;
        const data = res.bars.map((b) => ({
          time: b.time as Time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })) as CandlestickData<Time>[];
        series.current.setData(data);
        chart.current?.timeScale().fitContent();
        setMeta({ source: res.source, note: res.note, n: data.length });
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, interval]);

  // --- overlay our levels ------------------------------------------------
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const t = tokens();

    for (const l of lines.current) s.removePriceLine(l);
    lines.current = [];

    const specs: LevelSpec[] = [
      // Observed — solid. This is where it actually traded.
      { key: "spot", price: gex.spot, title: "spot", color: t["--text"], dashed: false },
      // Modeled — dashed. Every one of these is computed, not quoted.
      { key: "flip", price: gex.gamma_flip, title: "flip", color: t["--accent"], dashed: true },
      { key: "cw", price: gex.call_wall, title: "call wall", color: t["--down"], dashed: true },
      { key: "pw", price: gex.put_wall, title: "put wall", color: t["--up"], dashed: true },
      { key: "cn", price: gex.control_node, title: "magnet", color: t["--neutral"], dashed: true },
    ];

    for (const spec of specs) {
      // A null level is omitted entirely and named in the footer. Drawing a
      // placeholder line would be inventing a price.
      if (spec.price === null || !Number.isFinite(spec.price)) continue;
      lines.current.push(
        s.createPriceLine({
          price: spec.price,
          color: spec.color,
          lineWidth: 1,
          lineStyle: spec.dashed ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: true,
          title: spec.title,
        })
      );
    }

    levelPrices.current = specs
      .map((x) => x.price)
      .filter((p): p is number => p !== null && Number.isFinite(p));
    // Autoscale only re-runs on data change, so nudge it to pick up new levels.
    s.applyOptions({});
  }, [gex, meta]);

  const missing = [
    gex.gamma_flip === null && "gamma flip",
    gex.call_wall === null && "call wall",
    gex.put_wall === null && "put wall",
    gex.control_node === null && "magnet",
  ].filter(Boolean) as string[];

  return (
    <div className="pchart">
      <div className="pchart__bar">
        <div className="pchart__intervals" role="group" aria-label="Bar interval">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              className={`pchart__iv${iv === interval ? " pchart__iv--on" : ""}`}
              onClick={() => setInterval(iv)}
            >
              {iv}
            </button>
          ))}
        </div>
        <div className="pchart__legend">
          <Swatch color="var(--text)" solid label={`spot ${price(gex.spot)}`} />
          <Swatch color="var(--accent)" label={`flip ${price(gex.gamma_flip)}`} />
          <Swatch color="var(--down)" label={`call wall ${price(gex.call_wall)}`} />
          <Swatch color="var(--up)" label={`put wall ${price(gex.put_wall)}`} />
          <Swatch color="var(--neutral)" label={`magnet ${price(gex.control_node)}`} />
        </div>
      </div>

      <div className="pchart__canvas" ref={box} />

      {error && <p className="pchart__err">Bars unavailable — {error}</p>}

      <p className="pchart__note">
        Solid = observed price. Dashed = modeled level.
        {meta && ` ${meta.n} bars from ${meta.source}${meta.note ? ` (${meta.note})` : ""}.`}
        {missing.length > 0 && ` No ${missing.join(", ")} in range.`}
      </p>
    </div>
  );
}

function Swatch({ color, label, solid }: { color: string; label: string; solid?: boolean }) {
  return (
    <span className="pchart__key">
      <i
        className="pchart__dash"
        style={{
          borderTopColor: color,
          borderTopStyle: solid ? "solid" : "dashed",
        }}
      />
      {label}
    </span>
  );
}
