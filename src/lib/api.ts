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
  /** Runner-up strike, and how far ahead the leader is as a % of its own
   *  magnitude. Below ~15% the magnet is effectively a coin flip between the
   *  two — it has been observed flipping 13 points on a 0.1% move in spot. */
  control_node_runner_up?: number | null;
  control_node_margin_pct?: number | null;
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
  /** One or more roles joined with " + " when levels land on the same price. */
  role: string;
  tag: string;
  cls: "up" | "down" | "flip" | "watch" | string;
  /**
   * Two or more levels share this price. Not optional — the engine sets it on
   * every row. Confluence is the highest-conviction reaction point on the
   * board, so it is worth marking rather than leaving the reader to notice
   * that a role string happens to contain a plus sign.
   */
  confluence: boolean;
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
  /**
   * NULL when dir_n is 0, and that is correct rather than a gap: a neutral
   * call is graded but is not directional, so a book of only neutral calls has
   * no directional hit rate to report. The engine says "unknown" instead of
   * fabricating 0%.
   *
   * This was typed `number` while the engine sent null, so `tsc` passed and
   * the packaged board crashed on `.toFixed()` with a blank window. A type
   * that lies is worse than no type — it buys false confidence.
   */
  dir_hit_rate: number | null;
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
  /** The signal set currently producing calls, e.g. "v2-flow". */
  mix?: string;
  /** Set when the graded history spans more than one signal mix — the rate is
   *  then averaging two different systems and describes neither. */
  mixed_mixes?: string[] | null;
  by_mix?: Record<string, { n: number; wins: number }>;
}

/** Our level vs Unusual Whales' own. Surfaced, never auto-resolved. */
export interface LevelCheckRow {
  level: string;
  ours: number | null;
  uw: number | null;
  drift_pct?: number;
  /** Null when a known definitional difference makes grading meaningless. */
  agree: boolean | null;
  /** Why the two sides differ by design, when they do. */
  note?: string | null;
}

export interface Snapshot {
  generated_at: string;
  ticker: string;
  confirmer: string;
  tickers: string[];
  mock: boolean;
  provider: string;
  sources?: Record<string, string> | null;
  /** Provider's server-side stamp for the market data itself, ISO-8601 UTC.
   *  Null on feeds that publish no such thing (Yahoo) — the honest answer, and
   *  the UI then says "delayed" rather than inventing an age. */
  tape_time?: string | null;
  /** "premarket" | "market" | "postmarket", when the provider reports it. */
  market_time?: string | null;
  /**
   * True when this board was read from disk at startup rather than built now.
   * It is a REAL board from a past session, not a placeholder — which is
   * exactly why it must be labelled: last session's levels look identical to
   * this session's and are wrong. `generated_at` is deliberately left at its
   * original value so every staleness check keeps measuring the real age.
   */
  restored?: boolean;
  restored_from?: string | null;
  restored_age_days?: number | null;
  uw_errors: Record<string, string>;
  level_check: LevelCheckRow[] | null;
  gex: Gex;
  expected_move: ExpectedMove;
  level_map: LevelMapRow[];
  levels: Record<string, number>;
  smt: { signal: string; lean: number; note: string };
  bias: Bias;
  brief: string;
  /** Who wrote the brief — a model reading and string formatting are
   *  different claims, and the prose alone can't tell you which. */
  brief_meta?: {
    source: "claude" | "template" | string;
    model: string | null;
    error: string | null;
    /** True when reused because the underlying read hasn't changed. */
    cached?: boolean;
    age_s?: number;
    /** Model calls spent on this ticker today — the cost meter. */
    calls_today?: number;
    budget_capped?: boolean;
  };
  /** Event risk. `kind: "landed"` means news that already published — this is
   *  NOT a forward calendar, and the two call for opposite trades. */
  news: {
    high_impact: boolean;
    headline: string;
    items: Array<{ time: string; event: string; impact: string }>;
    kind?: "landed" | "unavailable" | string;
    level?: "high" | "medium" | "none" | "unknown" | string;
    why?: string;
    window_hours?: number;
    drivers?: Array<{
      event: string;
      impact: string;
      source: string;
      title: string;
      age_hours: number;
      primary: boolean;
    }>;
    headlines?: Array<{
      title: string;
      source: string;
      age_hours: number | null;
      tier: string;
      link: string;
    }>;
    feed_errors?: Record<string, string>;
    feed_count?: number;
    error?: string;
  };
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
  net_flow: NetFlow | null;
  max_pain: Array<{ expiry: string; strike: number }> | null;
  matrix: GexMatrix | null;
  darkpool: DarkPoolPrint[] | null;
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

export interface NewsItem {
  title: string;
  link: string;
  summary: string;
  /** Epoch seconds, or null — an undated item stays undated. */
  published: number | null;
  source: string;
  source_id: string;
  category: string;
  tier: "primary" | "market" | "wire" | string;
  macro_terms: string[];
  ticker_match: boolean;
  relevance: number;
  age_hours: number | null;
  /** relevance decayed by age — what the default ordering uses. */
  rank: number;
}

export interface News {
  items: NewsItem[];
  sources: Record<string, { name: string; n: number; category: string; tier: string }>;
  /** Per-feed failure reasons. Rendered, never swallowed. */
  errors: Record<string, string>;
  fetched_at: number;
  ticker: string;
  sort: string;
  total_before_dedupe: number;
  cached: boolean;
}

export interface Sessions {
  now_et: string;
  date: string;
  weekday: string;
  trading_day: boolean;
  /** Why the market is shut — "Weekend" and "Thanksgiving" are different. */
  closed_reason: string | null;
  early_close: string | null;
  early_close_name: string | null;
  phase: "open" | "pre" | "closed";
  minutes_until: number | null;
  until_label: string | null;
  sessions: Array<{
    id: string; label: string; kind: string; active: boolean;
    start: string; end: string;
  }>;
  windows: Array<{
    id: string; label: string; active: boolean; note: string;
    start: string; end: string;
  }>;
  my_window: { active: boolean; start: string; end: string; note: string };
}

export interface FlowRow {
  at: string;
  ticker: string;
  right: "C" | "P";
  spot: number;
  strike: number;
  expiry: string;
  dte: number | null;
  size: number;
  price: number;
  premium: number;
  trade_type: string;
  side: string;
  sentiment: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  volume: number | null;
  open_interest: number | null;
  vol_oi: number | null;
  /** True for synthetic scaffold rows. Never mistakable for a real print. */
  mock: boolean;
}

export interface Flow {
  rows: FlowRow[];
  source: "mock" | "unusual_whales" | string;
  /** False means these are not real prints — the UI must say so loudly. */
  available: boolean;
  note: string;
}

export interface ChatStatus {
  enabled: boolean;
  model: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/**
 * Stream a reply, calling `onChunk` as text arrives.
 *
 * The engine sends plain text chunks, not SSE — there is no event framing to
 * parse, so this just decodes and appends. Returns the full text.
 *
 * `signal` lets the UI abort a run in flight; a half-finished answer left
 * streaming into a panel you have navigated away from is both confusing and
 * billable.
 */
export async function chatStream(
  ticker: string,
  message: string,
  history: ChatTurn[],
  onChunk: (text: string) => void,
  signal?: AbortSignal
): Promise<string> {
  const res = await fetch(`${ENGINE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, message, history }),
    signal,
  });
  if (!res.ok) throw new Error(`chat ${res.status}: ${await res.text()}`);
  if (!res.body) throw new Error("chat returned no body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    full += chunk;
    onChunk(chunk);
  }
  return full;
}

/** One recorded call, plus the reasoning captured when it was made. */
export interface JournalRecord {
  date: string;
  ticker: string;
  bias: string;
  score: number;
  predicted_dir: "up" | "down" | "flat" | string;
  spot: number;
  note?: string;
  outcome: {
    correct: boolean;
    actual_dir: string;
    move_pct: number;
    rule?: string;
    note?: string;
  } | null;
  /** Absent on records written before context capture existed. */
  context?: {
    regime?: string;
    net_gex?: number;
    gamma_flip?: number | null;
    call_wall?: number | null;
    put_wall?: number | null;
    control_node?: number | null;
    atm_iv?: number;
    put_call_ratio?: number;
    expected_move?: { dollars: number; pct: number; low: number; high: number };
    conviction?: string;
    summary?: string;
    signals?: Array<{ name: string; lean: number; weight: number; reason: string }>;
    smt?: string;
    news?: string;
    news_level?: string;
    provider?: string;
  };
}

export interface CaptureDay {
  date: string;
  weekday: string | null;
  trading_day: boolean | null;
  provider: string | null;
  uw_key: boolean | null;
  tickers: string[];
  graded: number | null;
  files: number;
  bytes: number;
  errors: Record<string, string>;
  ticker_errors: Record<string, Record<string, string>>;
}

export interface CaptureStatus {
  dir: string;
  days: CaptureDay[];
  /** Trading days with no capture at all — permanent holes. */
  missing_trading_days: string[];
  /**
   * Non-null means the audit FAILED and the list above is unknown rather than
   * empty. This used to be swallowed, so a crash in the check rendered as
   * "no missed days" — a false negative on the one thing the panel exists for,
   * and unrecoverable by the time you noticed.
   */
  missing_error?: string | null;
  /** Gaps are only counted from the first capture onward. */
  audited_since?: string | null;
  total_bytes: number;
}

export interface Journal {
  ticker: string;
  records: JournalRecord[];
  stats: TrackRecord;
}

export interface CalEvent {
  kind: "econ" | "earnings" | string;
  title: string;
  country: string;
  impact: "High" | "Medium" | "Low" | "Holiday" | string;
  /** 3 = high impact. Used for ordering and emphasis. */
  rank: number;
  date: string;
  time: string;
  forecast: string;
  previous: string;
  url: string;
  ticker?: string;
  days_out?: number;
}

export interface Calendar {
  days: Array<{
    date: string;
    weekday: string;
    is_today: boolean;
    is_past: boolean;
    events: CalEvent[];
  }>;
  /** Next high-impact event still ahead. */
  headline: CalEvent | null;
  high_impact: CalEvent[];
  upcoming_earnings: CalEvent[];
  counts: {
    econ: number;
    earnings_this_week: number;
    earnings_upcoming: number;
    total: number;
  };
  errors: Record<string, string>;
  fetched_at: number;
  source: string;
  cached?: boolean;
  /** True when the upstream failed and this is the last good calendar. */
  stale?: boolean;
  age_s?: number;
  /**
   * Every event in the week is already past — the normal state from Friday
   * evening onward, since Forex Factory's file does not roll over until the
   * week turns and publishes no next-week feed. NOT optional: the engine sets
   * it on every payload including the cached and failed paths. Typing it `?`
   * would let a missing field read as `false`, which is the same class of lie
   * as the `dir_hit_rate: number` that hid a null and blanked the screen.
   */
  spent: boolean;
}

export interface Health {
  ok: boolean;
  warm: string[];
  pid: number;
  started_at: string;
  provider: string;
  uw_key_set: boolean;
  anthropic_key_set: boolean;
  /** Today's UW request usage. Null unless UW is the live provider. */
  uw_budget?: UwBudget | null;
}

export interface NetFlow {
  available: boolean;
  note?: string;
  /** Call premium minus put premium, in dollars. */
  net: number;
  call_premium: number;
  put_premium: number;
  net_delta: number;
  /** Share of volume that traded at the ask — aggressive buying. Null if none. */
  call_ask_pct: number | null;
  put_ask_pct: number | null;
  series: Array<{ t: string | null; v: number }>;
  ticks: number;
}

export interface GexMatrix {
  expiries: Array<{ label: string; dte: number | null }>;
  rows: Array<{
    strike: number;
    /** One cell per expiry, aligned to `expiries`. Null = no open interest. */
    cells: Array<number | null>;
    total: number;
    at_spot: boolean;
    /** "flip" | "put wall" | "call wall" | "magnet" when this row is one. */
    level?: string | null;
  }>;
  /** Grid-wide magnitude for a single shared colour scale. */
  max_abs: number;
  spot: number;
  loaded: number;
}

export interface Change {
  key: string;
  label: string;
  before: number | string | null;
  after: number | string | null;
  move: number | null;
  pct: number | null;
  /** Move as a share of spot — the only scale comparable across tickers. */
  of_spot: number | null;
  /** Decision impact, not magnitude. 0 = regime flip, 7 = the call itself. */
  rank: number;
  kind: "level" | "regime" | "size" | "vol" | "call";
  note: string | null;
  /** False when the field exists on only one side — not a move. */
  known: boolean;
}

export interface Changes {
  available: boolean;
  baseline_date: string | null;
  baseline_at?: string | null;
  age_days?: number | null;
  changes: Change[];
  note: string | null;
  /** Baseline older than a long weekend — it is not "since yesterday". */
  stale_baseline?: boolean;
}

export interface DarkPoolPrint {
  ticker: string | null;
  price: number;
  size: number;
  premium: number;
  at: string | null;
  market_center: string | null;
  nbbo_bid: number | null;
  nbbo_ask: number | null;
  /** 0 = printed at the bid, 1 = at the ask. Null when NBBO is missing. */
  spread_pos: number | null;
  /** Inferred from spread_pos — dark pool prints carry no aggressor flag. */
  lean: "buy" | "sell" | "mid" | null;
  canceled: boolean;
}

export interface UwBudget {
  used: number;
  limit: number;
  remaining: number;
  pct: number;
  date: string;
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
  chatStatus: () => req<ChatStatus>("/api/chat/status"),
  journal: (ticker: string) =>
    req<Journal>(`/api/journal?ticker=${encodeURIComponent(ticker)}`),
  capture: () => req<CaptureStatus>("/api/capture"),
  changed: (ticker: string) =>
    req<Changes>(`/api/changed?ticker=${encodeURIComponent(ticker)}`),
  saveNote: (ticker: string, date: string, note: string) =>
    req<{ ok: boolean }>("/api/journal/note", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, date, note }),
    }),
  sessions: () => req<Sessions>("/api/sessions"),
  calendar: (refresh = false) =>
    req<Calendar>(`/api/calendar${refresh ? "?refresh=true" : ""}`),
  flow: (ticker: string, limit = 100) =>
    req<Flow>(`/api/flow?ticker=${encodeURIComponent(ticker)}&limit=${limit}`),
  news: (ticker: string, sort = "relevance", refresh = false) =>
    req<News>(
      `/api/news?ticker=${encodeURIComponent(ticker)}&sort=${sort}${refresh ? "&refresh=true" : ""}`
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
