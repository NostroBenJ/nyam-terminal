/**
 * Read design tokens as real color strings.
 *
 * Canvas cannot resolve `var(--up)` — Lightweight Charts needs a literal like
 * "#26a69a". Rather than duplicating hexes into TypeScript (where they'd drift
 * from the stylesheet the moment either changes), this resolves them from the
 * live computed style, so tokens.css stays the single source of truth.
 */

let cache: Record<string, string> | null = null;

const NAMES = [
  "--bg", "--panel", "--panel-2", "--line",
  "--text", "--muted", "--dim",
  "--up", "--down", "--accent", "--neutral",
] as const;

export type TokenName = (typeof NAMES)[number];

export function tokens(): Record<TokenName, string> {
  if (cache) return cache as Record<TokenName, string>;
  const cs = getComputedStyle(document.documentElement);
  const out = {} as Record<string, string>;
  for (const n of NAMES) out[n] = cs.getPropertyValue(n).trim();
  cache = out;
  return out as Record<TokenName, string>;
}
