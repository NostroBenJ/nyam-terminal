import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { api, waitForEngine, type Health, type Snapshot } from "./lib/api";
import { ageSeconds, ageLabel, freshness } from "./lib/format";
import { Panel, Empty } from "./components/Panel";
import { TopBar } from "./components/TopBar";
import { UwBudget } from "./components/UwBudget";
import { DarkPool } from "./components/DarkPool";
import { GexMatrix } from "./components/GexMatrix";
import { NetFlow } from "./components/NetFlow";
import { TodaysTrade } from "./components/TodaysTrade";
import { Gauge } from "./components/Gauge";
import { Sidebar, SECTIONS, type SectionId } from "./components/Sidebar";
import { GexProfile } from "./components/GexProfile";
import { PriceChart } from "./components/PriceChart";
import { BiasPanel } from "./components/BiasPanel";
import { LevelMap } from "./components/LevelMap";
import { NewsRail } from "./components/NewsRail";
import { EventRisk } from "./components/EventRisk";
import { SessionClock } from "./components/SessionClock";
import { FlowScanner } from "./components/FlowScanner";
import { loadUi, saveUi } from "./lib/persist";
import { initTheme, subscribeTheme, type ThemeId } from "./lib/theme";
import { invalidateTokens } from "./lib/tokens";
import { openPanel, panelFromUrl, isTauri } from "./lib/windows";
import { Settings } from "./components/Settings";
import { Chat } from "./components/Chat";
import { Brief } from "./components/Brief";
import { Journal } from "./components/Journal";
import { WeekAhead } from "./components/WeekAhead";
import { CaptureStatus } from "./components/CaptureStatus";
import "./styles/tokens.css";
import "./styles/app.css";

const POLL_MS = 60_000;

export default function App() {
  const [ui] = useState(loadUi);
  /** Set in a detached window (?panel=flow); null in the main window. */
  const [detached] = useState(panelFromUrl);
  const [theme, setTheme] = useState<ThemeId>(initTheme);
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

  // Charts hold literal colour strings and cannot re-read a CSS variable, so
  // the token cache is dropped and the chart remounted (via its key) on change.
  useEffect(() =>
    subscribeTheme(() => {
      invalidateTokens();
      setTheme(document.documentElement.getAttribute("data-theme") as ThemeId);
    }),
  []);

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

  /** Pop-out control, shown on a panel header only in the main window. */
  const pop = (id: string) =>
    detached || !isTauri() ? undefined : (
      <button
        className="panel__pop"
        title="Open in its own window"
        onClick={() => void openPanel(id)}
      >
        ↗
      </button>
    );

  /**
   * One section's content. Shared by the shell and by detached windows so a
   * popped-out panel is literally the same component, not a second
   * implementation that can drift from it.
   */
  function renderSection(id: string) {
    switch (id) {
      case "board":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Price" subtitle={`${snap!.ticker} · our levels overlaid`}
                     {...panelProps} right={pop("chart")} grow>
                <PriceChart key={theme} gex={snap!.gex} ticker={snap!.ticker} />
              </Panel>
              <Panel title="Level map" subtitle="high to low" {...panelProps}>
                <LevelMap rows={snap!.level_map} spot={snap!.gex.spot} />
              </Panel>
            </div>
            <div className="grid__col">
              {/* Top of the right column, above everything: the one-glance
                  answer. Composed from plan + bias rather than computing
                  anything new — a second place that derives a trade is a
                  second place that can disagree with the first. */}
              <TodaysTrade snap={snap!} />
              {/* The gauge sits directly under the call because it answers the
                  next question: not "what's the lean" but "where am I standing
                  relative to the levels that produced it". */}
              <Panel title="Position" subtitle="where price sits between the walls"
                     {...panelProps}>
                <Gauge gex={snap!.gex} />
              </Panel>
              <Panel title="Session" subtitle="where you are in the day">
                <SessionClock compact />
              </Panel>
              <Panel title="Net premium" subtitle={`${snap!.ticker} · calls minus puts, today`}
                     {...panelProps}>
                <NetFlow flow={snap!.net_flow} />
              </Panel>
              <Panel title="Bias" subtitle={`confirmed vs ${snap!.confirmer}`} {...panelProps}>
                <BiasPanel bias={snap!.bias} />
              </Panel>
              <Panel title="Event risk" subtitle="what News Risk is reading" {...panelProps}>
                <EventRisk news={snap!.news} />
              </Panel>
              <Panel title="Headlines" subtitle={snap!.ticker} right={pop("news")}>
                <NewsRail ticker={snap!.ticker} compact />
              </Panel>
            </div>
          </div>
        );

      case "chart":
        return (
          <div className="view__single">
            <Panel title="Price" subtitle={`${snap!.ticker} · our levels overlaid`}
                   {...panelProps} grow>
              <PriceChart key={theme} gex={snap!.gex} ticker={snap!.ticker} />
            </Panel>
          </div>
        );

      case "gamma":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Gamma exposure by strike"
                     subtitle={`${snap!.gex.profile.length} strikes loaded`}
                     {...panelProps} right={pop("gamma")} grow>
                <GexProfile gex={snap!.gex} />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel title="Gamma matrix"
                     subtitle={`strike × expiry · ${snap!.matrix?.loaded ?? 0} expiries`}
                     {...panelProps}>
                <GexMatrix grid={snap!.matrix} />
              </Panel>
              <Panel title="Level map" subtitle="high to low" {...panelProps}>
                <LevelMap rows={snap!.level_map} spot={snap!.gex.spot} />
              </Panel>
              <Panel title="Level check" subtitle="ours vs Unusual Whales">
                {snap!.level_check?.length ? (
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
                      {snap!.level_check.map((r) => (
                        <tr key={r.level}>
                          <td className="levels__role">
                            {r.level}
                            {/* A level with a known definitional difference is
                                marked, not graded. Showing it as red drift
                                every single day would train you to ignore the
                                one panel meant to catch real breakage. */}
                            {r.note && (
                              <span className="lc__why" title={r.note}>
                                by design
                              </span>
                            )}
                          </td>
                          <td className="num">{r.ours ?? "—"}</td>
                          <td className="num">{r.uw ?? "—"}</td>
                          <td className={`num ${r.agree === null ? "muted"
                            : `levels__tag--${r.agree ? "up" : "down"}`}`}>
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
              <Panel title="Trade plan" subtitle={snap!.plan?.regime} {...panelProps}>
                <p className="bias__summary">{snap!.plan?.headline}</p>
                <table className="levels">
                  <tbody>
                    {snap!.plan?.rows?.map((r, i) => (
                      <tr key={`${r.level}-${i}`}>
                        <td className={`levels__price num levels__price--${r.tone}`}>{r.level}</td>
                        <td className="levels__role">{r.label}</td>
                        <td className={`levels__tag levels__tag--${r.tone}`}>{r.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="disclaimer">{snap!.plan?.bias_note}</p>
              </Panel>
            </div>
          </div>
        );

      case "flow":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Flow scanner"
                     subtitle={`${snap!.ticker} · every print, filterable`}
                     right={pop("flow")} grow>
                <FlowScanner ticker={snap!.ticker} />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel title="Dark pool"
                     subtitle={`${snap!.ticker} · off-exchange prints, placed in the spread`}
                     {...panelProps}>
                <DarkPool prints={snap!.darkpool} />
              </Panel>
            </div>
          </div>
        );

      case "ask":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Ask" subtitle={`grounded in the live ${snap!.ticker} snapshot`}
                     right={pop("ask")} grow>
                <Chat ticker={snap!.ticker} />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel title="Morning brief" subtitle={snap!.ticker} {...panelProps}>
                <Brief snap={snap!} />
              </Panel>
            </div>
          </div>
        );

      case "news":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Market news"
                     subtitle={`ranked by relevance, decayed by age · ${snap!.ticker}`}
                     right={pop("news")} grow>
                <NewsRail ticker={snap!.ticker} />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel title="Week ahead" subtitle="scheduled — not yet happened">
                <WeekAhead />
              </Panel>
            </div>
          </div>
        );

      case "journal":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel
                title="Journal"
                subtitle={`${snap!.ticker} · every call and why it was made`}
                right={pop("journal")}
                grow
              >
                <Journal ticker={snap!.ticker} />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel title="Recorder" subtitle="headless daily capture">
                <CaptureStatus />
              </Panel>
            </div>
          </div>
        );

      case "sources":
        return (
          <div className="grid">
            <div className="grid__col">
              <Panel title="Appearance & windows" subtitle="theme and screen layout" grow>
                <Settings />
              </Panel>
            </div>
            <div className="grid__col">
              <Panel
                title="UW request budget"
                subtitle={`today · ${health?.uw_budget ? "measured, not estimated" : "inactive"}`}
              >
                <UwBudget budget={health?.uw_budget} />
              </Panel>
              <Panel title="Data sources" subtitle="what is actually feeding this screen">
                <table className="levels">
                  <tbody>
                    <tr>
                      <td className="levels__role">Mode</td>
                      <td>
                        <span className={`chip chip--${snap!.mock ? "warn" : "up"}`}>
                          {snap!.mock ? "MOCK" : snap!.provider.toUpperCase()}
                        </span>
                      </td>
                      <td className="levels__role">
                        {snap!.mock
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
          </div>
        );

      default:
        return <div className="view__single"><Empty>Unknown panel “{id}”.</Empty></div>;
    }
  }

  // A detached window renders ONE section and nothing else — no rail, no
  // duplicate top bar competing for the little vertical space a second screen
  // has. It shares the engine, so its numbers cannot drift from the board's.
  if (detached) {
    return (
      <div className="app app--detached">
        <div className="detached__bar">
          <span className="detached__title">{detached.toUpperCase()}</span>
          <span className="detached__ticker num">{snap.ticker}</span>
          <span className="detached__spot num">{snap.gex.spot.toFixed(2)}</span>
          {snap.mock && <span className="chip chip--warn">MOCK</span>}
          <span className={`age age--${fresh}`}>
            <i className="age__dot" />
            {age}
          </span>
        </div>
        <main className="view">{renderSection(detached)}</main>
      </div>
    );
  }

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
      {/* A restored board is a REAL board from a previous session, which is
          exactly what makes it dangerous: last session's levels look identical
          to this session's and are wrong. It is shown because painting it in
          0.8s beats an empty window for nine seconds — but never without
          saying so, and the banner clears the moment a live build lands. */}
      {snap.restored && (
        <div className="banner banner--warn">
          Showing the last saved board from {snap.restored_from}
          {snap.restored_age_days ? ` (${snap.restored_age_days} day${snap.restored_age_days === 1 ? "" : "s"} old)` : ""} —
          these are not current levels. Refreshing now.
        </div>
      )}

      <div className="shell">
        <Sidebar active={section} onSelect={goSection} />

        {/* No AnimatePresence mode="wait" here, deliberately. That holds the
            incoming section until the outgoing one finishes animating, and
            Motion animates on requestAnimationFrame — which browsers throttle
            or stop entirely for occluded, minimised or background windows. A
            detached panel sitting behind another window would deadlock
            mid-switch and simply stop responding to the rail. The content
            swaps immediately; the fade is decoration on top of a swap that has
            already happened. */}
        <motion.main
          key={section}
          className="view"
          initial={reduceMotion ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.12, ease: "easeOut" }}
        >
          {renderSection(section)}
        </motion.main>
      </div>
    </div>
  );
}
