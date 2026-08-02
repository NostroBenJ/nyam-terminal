import { useEffect, useState } from "react";
import { THEMES, applyTheme, loadTheme, type ThemeId } from "../lib/theme";
import {
  applyDuoLayout, isTauri, listMonitors, openPanel, type MonitorInfo,
} from "../lib/windows";
import { SECTIONS } from "./Sidebar";

/**
 * Appearance and window layout.
 *
 * Themes are previewed as swatches rather than described, because "professional
 * and clean" is not a spec — you pick it by looking at it.
 */
export function Settings() {
  const [theme, setTheme] = useState<ThemeId>(loadTheme);
  const [monitors, setMonitors] = useState<MonitorInfo[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const tauri = isTauri();

  useEffect(() => {
    void listMonitors().then(setMonitors);
  }, []);

  const pick = (id: ThemeId) => {
    setTheme(id);
    applyTheme(id);
  };

  const detachable = SECTIONS.filter((s) => !s.blocked && s.id !== "sources");

  return (
    <div className="settings">
      <section className="settings__block">
        <h3 className="panel-title">Theme</h3>
        <div className="themes">
          {THEMES.map((t) => (
            <button
              key={t.id}
              className={`themecard${t.id === theme ? " themecard--on" : ""}`}
              onClick={() => pick(t.id)}
              aria-pressed={t.id === theme}
            >
              <span className="themecard__swatches">
                {t.swatch.map((c) => (
                  <i key={c} className="themecard__sw" style={{ background: c }} />
                ))}
              </span>
              <span className="themecard__label">{t.label}</span>
              <span className="themecard__note">{t.note}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="settings__block">
        <h3 className="panel-title">Displays</h3>
        {!tauri ? (
          <p className="empty">Window layout needs the desktop app.</p>
        ) : monitors.length === 0 ? (
          <p className="empty">Detecting displays…</p>
        ) : (
          <table className="levels">
            <tbody>
              {monitors.map((m) => (
                <tr key={m.index}>
                  <td className="levels__role">{m.name}</td>
                  <td className="num">
                    {m.width}×{m.height}
                  </td>
                  <td className="num levels__dist">{m.scale}×</td>
                  <td>
                    {m.primary && <span className="chip chip--quiet">primary</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tauri && (
          <div className="settings__actions">
            <button
              className="btn"
              onClick={async () => setNote((await applyDuoLayout()).note)}
              disabled={!tauri}
            >
              Duo layout
            </button>
            <span className="settings__hint">
              Board stays here; Flow and News move to the second screen.
            </span>
          </div>
        )}
        {note && <p className="settings__note">{note}</p>}
      </section>

      <section className="settings__block">
        <h3 className="panel-title">Detach a panel</h3>
        <p className="settings__hint">
          Each opens as a real window sharing this engine — the numbers cannot
          disagree with the main board.
        </p>
        <div className="settings__actions">
          {detachable.map((s) => (
            <button
              key={s.id}
              className="btn"
              onClick={() => void openPanel(s.id)}
              disabled={!tauri}
            >
              {s.label} ↗
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
