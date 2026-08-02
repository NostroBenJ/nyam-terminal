import type { Snapshot } from "../lib/api";
import { Markdown } from "./Markdown";

/**
 * The morning brief — the same signals as the Bias panel, written out.
 *
 * It always says WHO WROTE IT. A model-written brief and a templated one read
 * almost identically, both fluent prose about the same levels, but they are
 * different claims: one is a language model's reading, the other is string
 * formatting. You cannot tell from the text, so the panel labels it.
 */
export function Brief({ snap }: { snap: Snapshot }) {
  const meta = snap.brief_meta;
  const fromClaude = meta?.source === "claude";

  return (
    <div className="brief">
      <div className="brief__head">
        <span className={`chip chip--${fromClaude ? "up" : "quiet"}`}>
          {fromClaude ? `WRITTEN BY ${(meta?.model ?? "claude").toUpperCase()}` : "TEMPLATED"}
        </span>
        {!fromClaude && meta?.error && (
          <span className="brief__err" title={meta.error}>
            {meta.error.startsWith("No ANTHROPIC")
              ? "no API key — set one in Sources"
              : `model call failed: ${meta.error}`}
          </span>
        )}
      </div>

      <Markdown text={snap.brief} />

      <p className="disclaimer">
        {fromClaude
          ? "A model's reading of the same numbers on this screen. It can be wrong about them; the panels are the source of truth."
          : "Generated from the signals by string formatting — no model involved."}
      </p>
    </div>
  );
}
