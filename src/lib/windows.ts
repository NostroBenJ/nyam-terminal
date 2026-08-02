/**
 * Multi-window helpers.
 *
 * Detached panels are real OS windows sharing one engine, so a level in a
 * popped-out window is the identical number the main board shows.
 *
 * Everything here degrades gracefully outside Tauri (a plain browser tab
 * during `npm run dev` in Chrome), because a missing IPC bridge should disable
 * a button, not throw on render.
 */

export interface MonitorInfo {
  index: number;
  name: string;
  width: number;
  height: number;
  x: number;
  y: number;
  scale: number;
  primary: boolean;
}

/** True inside the Tauri shell; false in a plain browser tab. */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: inv } = await import("@tauri-apps/api/core");
  return inv<T>(cmd, args);
}

export async function listMonitors(): Promise<MonitorInfo[]> {
  if (!isTauri()) return [];
  try {
    return await invoke<MonitorInfo[]>("list_monitors");
  } catch {
    return [];
  }
}

export async function openPanel(panel: string, monitor?: number): Promise<void> {
  if (!isTauri()) return;
  await invoke("open_panel", { panel, monitor });
}

/**
 * Which panel this window is showing, from ?panel=... — absent in the main
 * window, which renders the full shell with its section rail.
 */
export function panelFromUrl(): string | null {
  try {
    return new URLSearchParams(window.location.search).get("panel");
  } catch {
    return null;
  }
}

/**
 * A stacked-screen layout for the ZenBook Duo.
 *
 * The board stays on the primary (upper) screen where you're looking most of
 * the time; the flow firehose and the news rail go to the lower screen, where
 * they can be scanned peripherally without covering the chart. This is the
 * arrangement the second screen is actually good for — reference material you
 * glance at, not the thing you're staring at.
 */
export async function applyDuoLayout(): Promise<{ ok: boolean; note: string }> {
  if (!isTauri()) return { ok: false, note: "Detached windows need the desktop app." };
  const monitors = await listMonitors();
  if (monitors.length < 2) {
    return {
      ok: false,
      note: `Only ${monitors.length || 1} display detected. Panels will open on this one — ` +
            `on a ZenBook Duo, extend to the second screen first.`,
    };
  }
  const secondary = monitors.find((m) => !m.primary) ?? monitors[1];
  await openPanel("flow", secondary.index);
  await openPanel("news", secondary.index);
  return { ok: true, note: `Flow and News opened on ${secondary.name}.` };
}
