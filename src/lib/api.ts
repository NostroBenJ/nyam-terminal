/**
 * Client for the Python engine.
 *
 * The engine is the only thing that computes numbers. Nothing in this file
 * derives a Greek, a level, or a lean — it transports them. Types here mirror
 * the snapshot `engine/pipeline.py:build_snapshot` returns; when that shape
 * changes, this changes with it rather than the UI guessing.
 */

const PORT = 8765;
export const ENGINE = `http://127.0.0.1:${PORT}`;

/* ---------------------------------------------------------------- types -- */

export interface GexPoint {
  strike: number;
  gex: number;
}

export interface Gex {
  spot: number;
  net_gex: number;
  regime: "positive" | "negative";
  /** null is a real answer: "no zero-gamma crossing within range". */
  gamma_flip: number | null;
  control_node: number | null;
  atm_iv: number;
  call_wall: number | null;
  put_wall: number | null;
  profile: GexPoint[];
  put_call_ratio: number;
  call_oi: number;
  put_oi: number;
  call_oi_change: number;
  put_oi_change: number;
}

export interface BiasSignal {
  name: string;
  /** -1 short, 0 neutral, +1 long. */
  lean: number;
  weight: number;
  reason: string;
}

export interface Bias {
  label: string;
  score: number;
  conviction: string;
  summary: string;
  signals: BiasSignal[];
  scenarios: string[];
}

export interface LevelMapRow {
  price: number;
  role: string;
  tag: string;
  cls: "up" | "down" | "flip" | "watch" | string;
}

export interface ExpectedMove {
  dollars: number;
  pct: number;
  low: number;
  high: number;
  dte: number;
}

export interface TrackRecord {
  n: number;
  wins: number;
  losses: number;
  hit_rate: number;
  /** Directional calls only — the number that matters. Baseline is 50. */
  dir_n: number;
  dir_hit_rate: number;
  by_type: Record<string, { n: number; wins: number; rate: number }>;
  recent: Array<{
    date: string;
    bias: string;
    predicted: string;
    actual: string;
    correct: boolean;
    move_pct: number;
  }>;
  pending: number;
  ticker: string;
  /** e.g. "open->12:00@0.175" — the rule each record was graded under. */
  rule: string;
  mixed_rules: string[] | null;
}

/** Our level vs Unusual Whales' own. Surfaced, never auto-resolved. */
export interface LevelCheckRow {
  level: string;
  ours: number | null;
  uw: number | null;
  drift_pct?: number;
  agree: boolean | null;
}

export interface Snapshot {
  generated_at: string;
  ticker: string;
  confirmer: string;
  tickers: string[];
  mock: boolean;
  provider: string;
  sources?: Record<string, string> | null;
  uw_errors: Record<string, string>;
  level_check: LevelCheckRow[] | null;
  gex: Gex;
  expected_move: ExpectedMove;
  level_map: LevelMapRow[];
  levels: Record<string, number>;
  smt: { signal: string; lean: number; note: string };
  bias: Bias;
  brief: string;
  news: { high_impact: boolean; headline: string; items: Array<{ time: string; event: string; impact: string }> };
  track: TrackRecord;
  plan: {
    regime: string;
    headline: string;
    rows: Array<{ level: number; label: string; action: string; tone: string; why: string }>;
    bias_note: string;
    trap_door: number | null;
  };
  trend: { arrow: string; text: string; cls: string };
  flow_alerts: unknown[] | null;
  darkpool: unknown[] | null;
}

/** One OHLC bar. `time` is epoch SECONDS for intraday, "YYYY-MM-DD" for daily. */
export interface Bar {
  time: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Bars {
  bars: Bar[];
  /** "mock" | "yahoo" | "unusual_whales" — the UI must be able to say which. */
  source: string;
  interval: string;
  /** Caveat text: delayed, synthetic, or why a fallback served this. */
  note: string;
}

export const INTERVALS = ["1m", "5m", "30m", "1h"] as const;
export type Interval = (typeof INTERVALS)[number];

export interface Health {
  ok: boolean;
  warm: string[];
  pid: number;
  started_at: string;
  provider: string;
  uw_key_set: boolean;
  anthropic_key_set: boolean;
}

/* --------------------------------------------------------------- client -- */

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ENGINE}${path}`, init);
  if (!res.ok) {
    throw new Error(`engine ${res.status} on ${path}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<Health>("/api/health"),
  bias: (ticker: string) => req<Snapshot>(`/api/bias?ticker=${encodeURIComponent(ticker)}`),
  /** Bars are a separate call from the snapshot on purpose — different refresh
   *  cadence, far larger payload, and one slow feed must not block the board. */
  bars: (ticker: string, interval: string, days = 5) =>
    req<Bars>(
      `/api/bars?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(interval)}&days=${days}`
    ),
  setTicker: (ticker: string) =>
    req<Snapshot>(`/api/ticker?ticker=${encodeURIComponent(ticker)}`, { method: "POST" }),
  refresh: (ticker: string) =>
    req<Snapshot>(`/api/refresh?ticker=${encodeURIComponent(ticker)}`, { method: "POST" }),
};

/**
 * Poll /api/health until the sidecar answers.
 *
 * The shell spawns the engine and the window can paint before the port is
 * open, so the first fetch losing the race is expected, not an error. Only a
 * sustained failure is worth showing the user.
 */
export async function waitForEngine(timeoutMs = 30_000): Promise<Health> {
  const deadline = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      return await api.health();
    } catch (e) {
      last = e;
      await new Promise((r) => setTimeout(r, 400));
    }
  }
  throw new Error(`engine did not respond within ${timeoutMs}ms: ${last}`);
}
