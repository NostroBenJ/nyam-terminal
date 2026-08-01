/**
 * Section rail.
 *
 * Follows the OptionsFlow decomposition — one section per job rather than one
 * giant board. A section that isn't usable yet is shown DISABLED with the
 * reason, not hidden: knowing the flow scanner exists and needs a key is more
 * useful than wondering whether you imagined it.
 */

export type SectionId = "board" | "gamma" | "flow" | "news" | "journal" | "sources";

export interface Section {
  id: SectionId;
  label: string;
  glyph: string;
  hint: string;
  /** Set when the section can't work yet — becomes the disabled reason. */
  blocked?: string;
}

export const SECTIONS: Section[] = [
  { id: "board", label: "Board", glyph: "▤", hint: "Price, gamma, bias — the morning read" },
  { id: "gamma", label: "Gamma", glyph: "⌗", hint: "Dealer positioning by strike" },
  // No longer blocked: the scanner is built and runs on synthetic rows, which
  // are labelled as such. Leaving it disabled would have hidden finished work
  // behind a key that hasn't arrived.
  { id: "flow", label: "Flow", glyph: "⇄", hint: "Options order flow scanner" },
  { id: "news", label: "News", glyph: "◈", hint: "Market-moving headlines" },
  { id: "journal", label: "Journal", glyph: "✓", hint: "Graded calls and hit rate" },
  { id: "sources", label: "Sources", glyph: "⚙", hint: "Feeds, providers, and what's live" },
];

export function Sidebar({
  active,
  onSelect,
  newsCount,
}: {
  active: SectionId;
  onSelect: (id: SectionId) => void;
  newsCount?: number;
}) {
  return (
    <nav className="rail" aria-label="Sections">
      {SECTIONS.map((s) => {
        const blocked = Boolean(s.blocked);
        return (
          <button
            key={s.id}
            className={`rail__item${s.id === active ? " rail__item--on" : ""}${
              blocked ? " rail__item--blocked" : ""
            }`}
            onClick={() => !blocked && onSelect(s.id)}
            disabled={blocked}
            title={blocked ? `${s.label} — ${s.blocked}` : `${s.label} — ${s.hint}`}
            aria-current={s.id === active ? "page" : undefined}
          >
            <span className="rail__glyph" aria-hidden="true">{s.glyph}</span>
            <span className="rail__label">{s.label}</span>
            {s.id === "news" && newsCount ? (
              <span className="rail__badge">{newsCount > 99 ? "99+" : newsCount}</span>
            ) : null}
            {blocked && <span className="rail__lock" aria-hidden="true">·</span>}
          </button>
        );
      })}
    </nav>
  );
}
