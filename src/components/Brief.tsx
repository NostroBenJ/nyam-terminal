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

  // The brief no longer blocks the board: it costs ~14s of a cold build and
  // nothing else depends on it, so the levels arrive first and the prose
  // follows. Saying "still writing" is the honest rendering of that gap — an
  // empty panel would read as "there is nothing to say about today".
  if (meta?.source === "pending") {
    return (
      <div className="brief">
        <div className="brief__head">
          <span className="chip chip--quiet">WRITING…</span>
        </div>
        <p className="brief__pending">
          The board is complete and current — every level, the bias and the
          matrix are computed. Only the written commentary is still being
          generated, and it will appear here on the next refresh.
        </p>
      </div>
    );
  }

  return (
    <div className="brief">
      <div className="brief__head">
        <span className={`chip chip--${fromClaude ? "up" : "quiet"}`}>
          {fromClaude ? `WRITTEN BY ${(meta?.model ?? "claude").toUpperCase()}` : "TEMPLATED"}
        </span>
        {/* Reused, not regenerated. Shown so a brief that hasn't changed in an
            hour doesn't read as a fresh take on the current tape. */}
        {meta?.cached && (
          <span className="chip chip--quiet" title="The underlying read hasn't changed, so the brief was reused rather than rewritten.">
            reused{meta.age_s ? ` · ${Math.round(meta.age_s / 60)}m` : ""}
          </span>
        )}
        {meta?.budget_capped && (
          <span className="chip chip--warn" title={meta.error ?? ""}>
            budget cap
          </span>
        )}
        {/* `!= null` catches BOTH null and undefined. This read `!== undefined`,
            and the engine sends null while the brief is still pending — so the
            block rendered with {null} in place of the count and the meter read
            " calls today" with no number in it. */}
        {meta?.calls_today != null && (
          <span className="brief__calls" title="Model calls spent on this ticker today">
            {meta.calls_today} call{meta.calls_today === 1 ? "" : "s"} today
          </span>
        )}
        {!fromClaude && meta?.error && !meta.budget_capped && (
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
