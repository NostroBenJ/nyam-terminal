/**
 * Persisted UI preferences.
 *
 * Only VIEW state lives here — which section you were on, which ticker, which
 * bar interval. Never market data. A cached price that survives a restart is
 * indistinguishable from a live one on the next render, which is precisely the
 * stale-data trap the rest of this app is built to avoid.
 */

const KEY = "nyam.ui.v1";

export interface UiState {
  section: string;
  ticker: string;
  interval: string;
  newsSort: "relevance" | "latest";
  /** Price axis stretches to contain the gamma levels. Default on — framing
   *  price against the structure is why this chart exists. */
  fitLevels: boolean;
}

const DEFAULTS: UiState = {
  section: "board",
  ticker: "SPY",
  interval: "5m",
  newsSort: "relevance",
  fitLevels: true,
};

export function loadUi(): UiState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<UiState>;
    // Merge over defaults so a key added in a later version can't leave the
    // app booting into an undefined section.
    return { ...DEFAULTS, ...parsed };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveUi(patch: Partial<UiState>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...loadUi(), ...patch }));
  } catch {
    // Storage disabled or full. Losing a preference is not worth an error path.
  }
}
