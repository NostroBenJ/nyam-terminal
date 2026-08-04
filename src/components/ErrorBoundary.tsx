import { Component, type ErrorInfo, type ReactNode } from "react";
import { ENGINE } from "../lib/api";

/**
 * Catches render errors so a crash reports itself instead of going blank.
 *
 * WHY THIS EXISTS. A React error unmounts the whole tree, leaving an empty
 * #root painted in the theme background — a dark rectangle with no sidebar, no
 * text, and no clue. In the packaged build there is no console to open and no
 * devtools to attach, so the only signal the user gets is "the app is broken"
 * and the only signal we get is their description of a colour. That is not
 * enough to fix anything, and it cost a full debugging session to learn.
 *
 * So: show the actual error and stack on screen, write it to disk through the
 * engine, and offer the two recoveries that actually work — reload, and clear
 * the persisted UI state. The second matters because saved state (section,
 * ticker, theme) is an input to rendering, which makes it a candidate cause of
 * any crash that only reproduces on one machine.
 */
type Props = { children: ReactNode };
type State = { error: Error | null; info: ErrorInfo | null; reported: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null, reported: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ info });
    // Best-effort, and never awaited: if the engine is the reason we crashed,
    // this fails too, and the on-screen report is what matters.
    let ui: Record<string, unknown> = {};
    try {
      ui = JSON.parse(localStorage.getItem("nyam.ui.v1") || "{}");
    } catch {
      /* unreadable persisted state is itself worth not crashing over */
    }
    fetch(`${ENGINE}/api/client-error`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: error.message,
        stack: error.stack || "",
        component: info.componentStack || "",
        section: String(ui.section ?? ""),
        ticker: String(ui.ticker ?? ""),
        theme: document.documentElement.getAttribute("data-theme") || "",
        ua: navigator.userAgent,
      }),
    })
      // `fetch` RESOLVES on a 404 — only a network failure rejects. Setting
      // reported=true on resolution alone made the crash screen claim the
      // report was saved when the endpoint did not exist and nothing was
      // written. A UI asserting a thing it did not verify is the failure this
      // whole app is built to avoid; check res.ok.
      .then((res) => this.setState({ reported: res.ok }))
      .catch(() => this.setState({ reported: false }));
  }

  render() {
    const { error, info, reported } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="crash">
        <h1 className="crash__title">The board crashed while rendering</h1>
        <p className="crash__lead">
          This is a bug in the app, not in your data. The details below are what
          it takes to fix it.{" "}
          {reported
            ? "Saved to the engine log, so it survives closing this window."
            : "The engine did not accept the report — copy the text below before closing."}
        </p>

        <div className="crash__box">
          <div className="crash__label">error</div>
          <pre className="crash__pre">{error.message}</pre>
        </div>

        {error.stack && (
          <div className="crash__box">
            <div className="crash__label">stack</div>
            <pre className="crash__pre">{error.stack}</pre>
          </div>
        )}

        {info?.componentStack && (
          <div className="crash__box">
            <div className="crash__label">component tree</div>
            <pre className="crash__pre">{info.componentStack.trim()}</pre>
          </div>
        )}

        <div className="crash__actions">
          <button className="btn" onClick={() => window.location.reload()}>
            Reload
          </button>
          <button
            className="btn"
            onClick={() => {
              // Saved state is an input to rendering, so it is a candidate
              // cause of a crash that happens on one machine and not another.
              // Clearing it is the one recovery the user can perform alone.
              try {
                localStorage.removeItem("nyam.ui.v1");
                localStorage.removeItem("nyam.theme");
              } catch {
                /* nothing useful to do if storage is unavailable */
              }
              window.location.reload();
            }}
          >
            Reset saved layout and reload
          </button>
        </div>

        <p className="crash__hint">
          The same report is appended to{" "}
          <code>engine/data_store/client_errors.log</code>, so it survives
          closing this window.
        </p>
      </div>
    );
  }
}
