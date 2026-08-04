/**
 * Deliberate crash, for proving the error boundary works.
 *
 * An error boundary that has never actually caught anything is a claim, not a
 * feature — and this one exists precisely because a silent failure cost a whole
 * debugging session. Set `?crash=1` on the URL (or run
 * `window.__nyamCrash()` in a console) to make the board throw during render
 * and confirm the report appears on screen AND lands in the engine log.
 *
 * Costs nothing when the flag is absent, which is always, in normal use.
 */
export function crashRequested(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("crash") === "1";
  } catch {
    return false;
  }
}

export function CrashProbe(): never {
  throw new Error(
    "CrashProbe: deliberate test crash (?crash=1). If you are seeing this " +
    "report rendered rather than a blank window, the error boundary works.",
  );
}
