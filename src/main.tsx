import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { CrashProbe, crashRequested } from "./components/CrashProbe";
import { ENGINE } from "./lib/api";

/**
 * Two safety nets, because a crash in the packaged build has no console.
 *
 * The boundary catches errors thrown during render. The listeners below catch
 * what a boundary structurally cannot: errors from event handlers, timers, and
 * rejected promises. Those do not unmount the tree, so they leave a board that
 * looks fine while quietly not working, which is the more dangerous of the two
 * failures on a screen used to make trades.
 */
function report(kind: string, message: string, stack: string) {
  try {
    fetch(`${ENGINE}/api/client-error`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: `[${kind}] ${message}`,
        stack,
        theme: document.documentElement.getAttribute("data-theme") || "",
        ua: navigator.userAgent,
      }),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    /* reporting must never itself throw */
  }
}

window.addEventListener("error", (e) =>
  report("uncaught", e.message, e.error?.stack || ""),
);
window.addEventListener("unhandledrejection", (e) =>
  report("unhandled-rejection", String(e.reason), e.reason?.stack || ""),
);

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      {crashRequested() ? <CrashProbe /> : <App />}
    </ErrorBoundary>
  </React.StrictMode>,
);
