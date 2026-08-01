import { useCallback, useEffect, useRef, useState } from "react";
import { api, waitForEngine, type Health, type Snapshot } from "./lib/api";
import { ageSeconds, ageLabel, freshness } from "./lib/format";
import { Panel, Empty } from "./components/Panel";
import { TopBar } from "./components/TopBar";
import { GexProfile } from "./components/GexProfile";
import { PriceChart } from "./components/PriceChart";
import { BiasPanel } from "./components/BiasPanel";
import { LevelMap } from "./components/LevelMap";
import { TrackRecord } from "./components/TrackRecord";
import "./styles/tokens.css";
import "./styles/app.css";

const POLL_MS = 60_000;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Ticks so the age chip decays on screen even when no fetch is running. */
  const [, tick] = useState(0);
  const active = useRef<string>("SPY");

  // Wait for the sidecar before the first fetch. The window can paint before
  // the engine's port is open, so an early failure is a race, not an outage.
  useEffect(() => {
    let cancelled = false;
    waitForEngine()
      .then(async (h) => {
        if (cancelled) return;
        setHealth(h);
        const t = h.warm[0] ?? "SPY";
        active.current = t;
        const s = await api.bias(t);
        if (!cancelled) setSnap(s);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async (fn: () => Promise<Snapshot>) => {
    setBusy(true);
    setError(null);
    try {
      setSnap(await fn());
    } catch (e) {
      // Surface it. Leaving the previous snapshot on screen without saying the
      // refresh failed is exactly the stale-data trap this UI must not set.
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const onTicker = useCallback(
    (t: string) => {
      active.current = t;
      void load(() => api.setTicker(t));
    },
    [load]
  );

  const onRefresh = useCallback(
    () => void load(() => api.refresh(active.current)),
    [load]
  );

  useEffect(() => {
    const poll = setInterval(() => void load(() => api.bias(active.current)), POLL_MS);
    const age = setInterval(() => tick((n) => n + 1), 5_000);
    return () => {
      clearInterval(poll);
      clearInterval(age);
    };
  }, [load]);

  if (error && !snap) {
    return (
      <div className="boot boot--error">
        <h1>Engine unreachable</h1>
        <p className="boot__detail">{error}</p>
        <p className="boot__hint">
          Start it manually with <code>cd engine &amp;&amp; python server.py</code>, then reload.
        </p>
      </div>
    );
  }

  if (!snap) {
    return (
      <div className="boot">
        <span className="boot__spinner" />
        <p>Starting engine…</p>
      </div>
    );
  }

  const ageSec = ageSeconds(snap.generated_at);
  const fresh = freshness(ageSec);
  const age = ageLabel(ageSec);

  return (
    <div className="app">
      <TopBar snap={snap} ageSec={ageSec} busy={busy} onTicker={onTicker} onRefresh={onRefresh} />

      {error && (
        <div className="banner banner--error">
          Refresh failed — the numbers below are from {age}. {error}
        </div>
      )}
      {snap.mock && (
        <div className="banner banner--warn">
          Mock data. Every number on this screen is synthetic and reflects no live market.
        </div>
      )}
      {health && !health.uw_key_set && !snap.mock && (
        <div className="banner banner--quiet">
          No Unusual Whales key set — running the free delayed path. Open-interest change,
          flow alerts and dark pool prints are unavailable.
        </div>
      )}

      <main className="grid">
        <div className="grid__col">
          <Panel
            title="Price"
            subtitle={`${snap.ticker} · our levels overlaid`}
            freshness={fresh}
            age={age}
            grow
          >
            <PriceChart gex={snap.gex} ticker={snap.ticker} />
          </Panel>

          <Panel
            title="Gamma exposure by strike"
            subtitle={`${snap.gex.profile.length} strikes loaded`}
            freshness={fresh}
            age={age}
          >
            <GexProfile gex={snap.gex} />
          </Panel>
        </div>

        <div className="grid__col">
          <Panel title="Bias" subtitle={`confirmed vs ${snap.confirmer}`} freshness={fresh} age={age}>
            <BiasPanel bias={snap.bias} />
          </Panel>

          <Panel title="Level map" subtitle="high to low" freshness={fresh} age={age}>
            <LevelMap rows={snap.level_map} spot={snap.gex.spot} />
          </Panel>

          <Panel title="Track record" subtitle={snap.track?.ticker} freshness={fresh} age={age}>
            <TrackRecord track={snap.track} />
          </Panel>

          <Panel title="Level check" subtitle="ours vs Unusual Whales">
            {snap.level_check?.length ? (
              <table className="levels">
                <thead>
                  <tr>
                    <th>level</th>
                    <th className="num">ours</th>
                    <th className="num">UW</th>
                    <th className="num">drift</th>
                  </tr>
                </thead>
                <tbody>
                  {snap.level_check.map((r) => (
                    <tr key={r.level}>
                      <td className="levels__role">{r.level}</td>
                      <td className="num">{r.ours ?? "—"}</td>
                      <td className="num">{r.uw ?? "—"}</td>
                      <td className={`num levels__tag--${r.agree === false ? "down" : "up"}`}>
                        {r.drift_pct === undefined ? "—" : `${r.drift_pct}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>
                No Unusual Whales levels to compare against. Once a key is set, our
                Black-Scholes levels appear beside theirs and any drift is reported —
                never resolved by overwriting ours.
              </Empty>
            )}
          </Panel>
        </div>
      </main>
    </div>
  );
}
