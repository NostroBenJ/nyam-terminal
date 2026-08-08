/**
 * Theme switching.
 *
 * Themes swap token VALUES; no component knows a theme's name. That keeps
 * every panel automatically theme-correct and means adding a theme is a
 * tokens.css edit, not a sweep through the components.
 *
 * Canvas is the exception worth knowing about: Lightweight Charts takes literal
 * colour strings, so it cannot re-read a CSS variable on its own. The token
 * cache is invalidated here and the chart is remounted on theme change.
 */

export const THEMES = [
  {
    id: "night",
    label: "Night",
    note: "The house style. Ink ground, engraved rules, copper reserved for the live reading.",
    swatch: ["#14161a", "#5fae8d", "#d4626c", "#d98c3f"],
  },
  {
    id: "day",
    label: "Day",
    note: "The same instrument, unlit. Bone ground and ink engraving — untested over a full session.",
    swatch: ["#e9e7e1", "#2f5d4a", "#8c2f39", "#b3651b"],
  },
  {
    id: "terminal",
    label: "Terminal",
    note: "Legacy. Deep navy-black, gold accent.",
    swatch: ["#0a0e14", "#26a69a", "#ef5350", "#e6b450"],
  },
  {
    id: "tradingview",
    label: "TradingView",
    note: "Legacy. Useful for A/B-ing a chart against a real TV window.",
    swatch: ["#131722", "#26a69a", "#ef5350", "#2962ff"],
  },
  {
    id: "carbon",
    label: "Carbon",
    note: "Legacy. Neutral near-black, highest contrast.",
    swatch: ["#0b0b0d", "#22c55e", "#f43f5e", "#eab308"],
  },
  {
    id: "slate",
    label: "Slate",
    note: "Legacy. Softer greys, lower contrast.",
    swatch: ["#161a20", "#3fb950", "#f85149", "#d29922"],
  },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

const KEY = "nyam.theme";
const DEFAULT: ThemeId = "night";

/** Token cache lives in tokens.ts; this is its invalidator. */
let onChange: Array<() => void> = [];

export function subscribeTheme(fn: () => void): () => void {
  onChange.push(fn);
  return () => {
    onChange = onChange.filter((f) => f !== fn);
  };
}

export function loadTheme(): ThemeId {
  try {
    const v = localStorage.getItem(KEY) as ThemeId | null;
    return v && THEMES.some((t) => t.id === v) ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function applyTheme(id: ThemeId): void {
  document.documentElement.setAttribute("data-theme", id);
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* storage disabled — the theme still applies for this session */
  }
  for (const fn of onChange) fn();
}

/** Call once at startup, before first paint, so there is no flash of default. */
export function initTheme(): ThemeId {
  const id = loadTheme();
  document.documentElement.setAttribute("data-theme", id);
  return id;
}
