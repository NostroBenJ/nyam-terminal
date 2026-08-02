import { useCallback, useEffect, useRef, useState } from "react";
import { api, chatStream, type ChatTurn } from "../lib/api";
import { Markdown } from "./Markdown";

/**
 * Ask questions about the board you're looking at.
 *
 * The point over a browser tab: the engine re-injects the CURRENT snapshot on
 * every turn, so the model is reasoning about the spot, levels and bias on
 * screen right now rather than a generic explanation of gamma. Old snapshots
 * are stripped from the history server-side — market data goes stale in
 * minutes, and a snapshot pinned in turn 1 would have it reasoning about a
 * half-hour-old price for the rest of the conversation.
 *
 * The API key never reaches this component. Everything goes through the
 * engine, which holds it.
 */

const SUGGESTIONS = [
  "What would invalidate the current lean?",
  "Why is the bias short when the magnet is above spot?",
  "If price loses the flip, what changes?",
  "How much should I trust the track record here?",
];

export function Chat({ ticker }: { ticker: string }) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [model, setModel] = useState("");
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .chatStatus()
      .then((s) => {
        setEnabled(s.enabled);
        setModel(s.model);
      })
      .catch(() => setEnabled(false));
  }, []);

  // Keep the newest text in view while a reply streams in.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, partial]);

  // Abort an in-flight reply if this unmounts — a stream left running into a
  // panel you navigated away from is both confusing and billable.
  useEffect(() => () => abort.current?.abort(), []);

  const send = useCallback(
    async (text: string) => {
      const msg = text.trim();
      if (!msg || streaming) return;
      setError(null);
      setInput("");
      const history = turns;
      setTurns([...history, { role: "user", content: msg }]);
      setStreaming(true);
      setPartial("");

      const ctl = new AbortController();
      abort.current = ctl;
      try {
        let acc = "";
        await chatStream(
          ticker,
          msg,
          history,
          (chunk) => {
            acc += chunk;
            setPartial(acc);
          },
          ctl.signal
        );
        setTurns((t) => [...t, { role: "assistant", content: acc }]);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(String(e));
      } finally {
        setStreaming(false);
        setPartial("");
        abort.current = null;
      }
    },
    [ticker, turns, streaming]
  );

  if (enabled === false) {
    return (
      <p className="empty">
        Chat is off — no <code>ANTHROPIC_API_KEY</code>. Add one to{" "}
        <code>engine/.env</code> (or run <code>set-key.ps1</code>) and restart.
        The rest of the app works without it.
      </p>
    );
  }

  return (
    <div className="chat">
      <div className="chat__log" ref={scroller}>
        {turns.length === 0 && !streaming && (
          <div className="chat__intro">
            <p className="chat__introtext">
              Grounded in the live {ticker} snapshot — the same numbers on screen.
            </p>
            <div className="chat__suggest">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chat__chip" onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className={`turn turn--${t.role}`}>
            <span className="turn__who">{t.role === "user" ? "you" : model || "claude"}</span>
            <div className="turn__body">
              {t.role === "user" ? <p className="md__p">{t.content}</p> : <Markdown text={t.content} />}
            </div>
          </div>
        ))}

        {streaming && (
          <div className="turn turn--assistant">
            <span className="turn__who">{model || "claude"}</span>
            <div className="turn__body">
              {partial ? <Markdown text={partial} /> : <span className="chat__thinking">…</span>}
            </div>
          </div>
        )}
      </div>

      {error && <p className="chat__error">{error}</p>}

      <form
        className="chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <input
          className="chat__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Ask about ${ticker}…`}
          disabled={streaming}
        />
        {streaming ? (
          <button type="button" className="btn" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="btn" disabled={!input.trim()}>
            Ask
          </button>
        )}
      </form>

      <p className="disclaimer">
        The model sees this snapshot, not your positions or your broker. It can be
        wrong about the numbers — the panels are the source of truth.
      </p>
    </div>
  );
}
