/** Display helpers. Formatting only — never arithmetic that changes a value. */

/** Compact dollar magnitude: 260169094 -> "+$260.2M". Sign is always explicit
 *  so direction never depends on color alone. */
export function money(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const sign = n < 0 ? "−" : "+";
  const a = Math.abs(n);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

export function price(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toFixed(dp);
}

export function pct(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const sign = n < 0 ? "−" : "+";
  return `${sign}${Math.abs(n).toFixed(dp)}%`;
}

export function int(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

/** Seconds since a snapshot was generated. Drives the staleness chip. */
export function ageSeconds(generatedAt: string): number | null {
  // Engine stamps "YYYY-MM-DD HH:MM:SS TZ" in America/New_York.
  const m = generatedAt.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  // The stamp is wall-clock in the engine's timezone. Comparing it to local
  // time is only meaningful when they match; when they don't, a negative or
  // absurd age is a signal we should not dress up as a real number.
  const t = new Date(+y, +mo - 1, +d, +h, +mi, +s).getTime();
  const age = (Date.now() - t) / 1000;
  return Number.isFinite(age) ? age : null;
}

export type Freshness = "live" | "delayed" | "stale" | "unknown";

export function freshness(ageSec: number | null): Freshness {
  if (ageSec === null || ageSec < -120) return "unknown";
  if (ageSec < 90) return "live";
  if (ageSec < 15 * 60) return "delayed";
  return "stale";
}

export function ageLabel(ageSec: number | null): string {
  if (ageSec === null) return "age unknown";
  const a = Math.max(0, Math.round(ageSec));
  if (a < 60) return `${a}s ago`;
  if (a < 3600) return `${Math.round(a / 60)}m ago`;
  return `${(a / 3600).toFixed(1)}h ago`;
}
