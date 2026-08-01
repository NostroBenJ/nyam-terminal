import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api, waitForEngine, type Health, type Snapshot } from "./lib/api";
import { ageSeconds, ageLabel, freshness } from "./lib/format";
import { Panel, Empty } from "./components/Panel";
import { TopBar } from "./components/TopBar";
import { Sidebar, SECTIONS, type SectionId } from "./components/Sidebar";
import { GexProfile } from "./components/GexProfile";
import { PriceChart } from "./components/PriceChart";
import { BiasPanel } from "./components/BiasPanel";
import { LevelMap } from "./components/LevelMap";
import { TrackRecord } from "./components/TrackRecord";
import { NewsRail } from "./components/NewsRail";
import { EventRisk } from "./components/EventRisk";
import { loadUi, saveUi } from "./lib/persist";
import "./styles/tokens.css";
import "./styles/app.css";

const POLL_MS = 60_000;

export default function App() {
  const [ui] = useState(loadUi);
  const [health, setHealth] = useState<Health | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<SectionId>(ui.section as SectionId);
  /** Ticks so the age chip decays on screen even when no fetch is running. */
  const [, tick] = useState(0);
  const active = useRef<string>(ui.ticker);
  const reduceMotion = useReducedMotion();

  const goSection = useCallback((id: SectionId) => {
    setSection(id);
    saveUi({ section: id });
  }, []);

  // Wait for the sidecar before the first fetch. The window can paint before
  // the engine's port is open, so an early failure is a race, not an outage.
  useEffect(() => {
    let cancelled = false;
    waitForEngine()
      .then(async (h) => {
        if (cancelled) return;
        setHealth(h);
        // Prefer the ticker you were last on over whatever the engine warmed.
        const t = active.current || h.warm[0] || "SPY";
        active.current = t;
        const s = await api.setTicker(t);
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
      saveUi({ ticker: t });
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
    const ageTimer = setInterval(() => tick((n) => n + 1), 5_000);
    return () => {
      clearInterval(poll);
      clearInterval(ageTimer);
    };
  }, [load]);

  // Ctrl/Cmd+1..6 jumps between sections — this is a keyboard tool.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const n = Number(e.key);
      if (n >= 1 && n <= SECTIONS.length) {
        const s = SECTIONS[n - 1];
        if (!s.blocked) {
          e.preventDefault();
          goSection(s.id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goSection]);

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
  const panelProps = { freshness: fresh, age };

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

      <div className="shell">
        <Sidebar active={section} onSelect={goSection} />

        <AnimatePresence mode="wait">
          <motion.main
            key={section}
            className="view"
            initial={reduceMotion ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? undefined : { opacity: 0 }}
            transition={{ duration: 0.12, ease: "easeOut" }}
          >
            {section === "board" && (
              <div className="grid">
                <div className="grid__col">
                  <Panel title="Price" subtitle={`${snap.ticker} · our levels overlaid`} {...panelProps} grow>
                    <PriceChart gex={snap.gex} ticker={snap.ticker} />
                  </Panel>
                  <Panel title="Level map" subtitle="high to low" {...panelProps}>
                    <LevelMap rows={snap.level_map} spot={snap.gex.spot} />
                  </Panel>
                </div>
                <div className="grid__col">
                  <Panel title="Bias" subtitle={`confirmed vs ${snap.confirmer}`} {...panelProps}>
                    <BiasPanel bias={snap.bias} />
                  </Panel>
                  <Panel title="Event risk" subtitle="what News Risk is reading" {...panelProps}>
                    <EventRisk news={snap.news} />
                  </Panel>
                  <Panel title="Headlines" subtitle={snap.ticker}>
                    <NewsRail ticker={snap.ticker} compact />
                  </Panel>
                </div>
              </div>
            )}

            {section === "gamma" && (
              <div className="grid">
                <div className="grid__col">
                  <Panel
                    title="Gamma exposure by strike"
                    subtitle={`${snap.gex.profile.length} strikes loaded`}
                    {...panelProps}
                    grow
                  >
                    <GexProfile gex={snap.gex} />
                  </Panel>
                </div>
                <div className="grid__col">
                  <Panel title="Level map" subtitle="high to low" {...panelProps}>
                    <LevelMap rows={snap.level_map} spot={snap.gex.spot} />
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
                  <Panel title="Trade plan" subtitle={snap.plan?.regime} {...panelProps}>
                    <p className="bias__summary">{snap.plan?.headline}</p>
                    <table className="levels">
                      <tbody>
                        {snap.plan?.rows?.map((r, i) => (
                          <tr key={`${r.level}-${i}`}>
                            <td className={`levels__price num levels__price--${r.tone}`}>{r.level}</td>
                            <td className="levels__role">{r.label}</td>
                            <td className={`levels__tag levels__tag--${r.tone}`}>{r.action}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="disclaimer">{snap.plan?.bias_note}</p>
                  </Panel>
                </div>
              </div>
            )}

            {section === "news" && (
              <div className="view__single">
                <Panel
                  title="Market news"
                  subtitle={`ranked by relevance, decayed by age · ${snap.ticker}`}
                  grow
                >
                  <NewsRail ticker={snap.ticker} />
                </Panel>
              </div>
            )}

            {section === "journal" && (
              <div className="view__single">
                <Panel title="Track record" subtitle={snap.track?.ticker} {...panelProps} grow>
                  <TrackRecord track={snap.track} />
                </Panel>
              </div>
            )}

            {section === "sources" && (
              <div className="view__single">
                <Panel title="Data sources" subtitle="what is actually feeding this screen">
                  <table className="levels">
                    <tbody>
                      <tr>
                        <td className="levels__role">Mode</td>
                        <td>
                          <span className={`chip chip--${snap.mock ? "warn" : "up"}`}>
                            {snap.mock ? "MOCK" : snap.provider.toUpperCase()}
                          </span>
                        </td>
                        <td className="levels__role">
                          {snap.mock
                            ? "Synthetic. Nothing here reflects a live market."
                            : "Live provider for the option chain."}
                        </td>
                      </tr>
                      <tr>
                        <td className="levels__role">Unusual Whales</td>
                        <td>
                          <span className={`chip chip--${health?.uw_key_set ? "up" : "quiet"}`}>
                            {health?.uw_key_set ? "KEY SET" : "NO KEY"}
                          </span>
                        </td>
                        <td className="levels__role">
                          {health?.uw_key_set
                            ? "Probe endpoint coverage before trusting a tier."
                            : "Flow, dark pool and OI change are unavailable without it."}
                        </td>
                      </tr>
                      <tr>
                        <td className="levels__role">Claude chat</td>
                        <td>
                          <span className={`chip chip--${health?.anthropic_key_set ? "up" : "quiet"}`}>
                            {health?.anthropic_key_set ? "KEY SET" : "NO KEY"}
                          </span>
                        </td>
                        <td className="levels__role">
                          Written brief falls back to a template without it.
                        </td>
                      </tr>
                      <tr>
                        <td className="levels__role">Engine</td>
                        <td className="num">pid {health?.pid ?? "—"}</td>
                        <td className="levels__role">127.0.0.1:8765</td>
                      </tr>
                    </tbody>
                  </table>
                  <p className="disclaimer">
                    Where a value can come from more than one place, the panel showing it says
                    which one served it.
                  </p>
                </Panel>
              </div>
            )}
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  );
}
