//! NYAM Terminal shell.
//!
//! The shell owns the Python engine's lifecycle. It spawns the engine on
//! startup and kills it on exit, so there is never an orphaned process holding
//! port 8765 after the window closes — which, left unhandled, makes the next
//! launch silently attach to a stale engine serving yesterday's numbers.
//!
//! The frontend talks to the engine over http://127.0.0.1:8765, not through
//! Tauri IPC. That keeps the engine independently runnable and testable
//! (`cd engine && python server.py`) and keeps the shell thin.

use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

/// Port the engine binds. Must match `ENGINE` in `src/lib/api.ts` and the
/// default in `engine/server.py`.
const ENGINE_PORT: u16 = 8765;

/// Handle to the spawned engine, so the exit hook can kill it.
struct Engine(Mutex<Option<Child>>);

/// How the engine will be started.
enum EngineKind {
    /// A PyInstaller build shipped alongside the app. No Python required.
    Bundled(std::path::PathBuf),
    /// `python server.py` from the source tree, for development.
    Source(std::path::PathBuf),
}

/// Find the source tree by walking up from the running executable.
fn source_dir() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    for up in [3usize, 4, 5] {
        let mut p = exe.clone();
        for _ in 0..up {
            p.pop();
        }
        let engine = p.join("engine");
        if engine.join("server.py").exists() {
            return Some(engine);
        }
    }
    None
}

fn bundled_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    let exe_name = if cfg!(windows) { "nyam-engine.exe" } else { "nyam-engine" };
    let dir = app.path().resource_dir().ok()?.join("engine");
    dir.join(exe_name).exists().then_some(dir)
}

/// Locate the engine. Which one wins DEPENDS ON THE BUILD, and getting this
/// backwards is expensive in both directions:
///
/// - In a RELEASE build, bundled must win. A packaged install falling through
///   to a developer's checkout would run different code than was shipped, and
///   the only symptom would be numbers that don't match.
/// - In a DEBUG build, source must win. Tauri copies bundled resources into
///   `target/debug/` during `tauri dev`, so preferring bundled means your
///   source edits silently do nothing while a stale binary serves the old API.
///   (Observed: two new endpoints 404'd against an engine built 13 minutes
///   before the code that added them.)
fn find_engine(app: &tauri::AppHandle) -> Option<EngineKind> {
    if cfg!(debug_assertions) {
        if let Some(d) = source_dir() {
            return Some(EngineKind::Source(d));
        }
        return bundled_dir(app).map(EngineKind::Bundled);
    }
    if let Some(d) = bundled_dir(app) {
        return Some(EngineKind::Bundled(d));
    }
    source_dir().map(EngineKind::Source)
}

/// Resolve the *real* interpreter path.
///
/// This matters more than it looks. On Windows `python` is frequently an app
/// alias or launcher shim that re-execs the actual interpreter as a separate
/// process. Spawning through it leaves us holding a handle to the shim while a
/// different pid owns the port, so killing our child orphans the engine and the
/// next launch silently attaches to a stale one serving yesterday's numbers.
/// Asking Python where it actually lives sidesteps the whole class of problem.
fn resolve_python() -> String {
    let fallback = if cfg!(windows) { "python" } else { "python3" };
    for candidate in [fallback, "python3", "python"] {
        if let Ok(out) = Command::new(candidate)
            .args(["-c", "import sys; print(sys.executable)"])
            .output()
        {
            if out.status.success() {
                let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !path.is_empty() && std::path::Path::new(&path).exists() {
                    return path;
                }
            }
        }
    }
    fallback.to_string()
}

fn spawn_engine(app: &tauri::AppHandle) -> Option<Child> {
    let kind = match find_engine(app) {
        Some(k) => k,
        None => {
            // Not fatal. The frontend polls /api/health and shows an explicit
            // "engine unreachable" screen, which is more useful than a panic
            // and lets a manually started engine still be picked up.
            eprintln!("[shell] no engine found; expecting a manually started one");
            return None;
        }
    };

    let (mut cmd, dir, how) = match kind {
        EngineKind::Bundled(dir) => {
            let exe = dir.join(if cfg!(windows) { "nyam-engine.exe" } else { "nyam-engine" });
            let mut c = Command::new(&exe);
            c.current_dir(&dir);
            (c, dir, "bundled".to_string())
        }
        EngineKind::Source(dir) => {
            let python = resolve_python();
            let mut c = Command::new(&python);
            c.arg("server.py").current_dir(&dir);
            (c, dir, format!("source via {python}"))
        }
    };
    cmd.arg("--port").arg(ENGINE_PORT.to_string());

    match cmd.spawn() {
        Ok(child) => {
            println!("[shell] engine pid {} ({}) in {}", child.id(), how, dir.display());
            Some(child)
        }
        Err(e) => {
            eprintln!("[shell] failed to spawn engine ({how}): {e}");
            None
        }
    }
}

/// Kill the engine and anything it spawned.
///
/// `child.kill()` alone only reaches the immediate process. Belt and braces on
/// Windows via taskkill /T, because a surviving engine holds port 8765 and the
/// symptom — a fresh window showing stale numbers — looks like a data bug
/// rather than a lifecycle one, which makes it expensive to diagnose.
fn kill_engine(mut child: Child) {
    let pid = child.id();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .output();
    }
    let _ = child.kill();
    let _ = child.wait();
    println!("[shell] engine {pid} stopped");
}

/// Open a panel in its own OS window.
///
/// Built for stacked-screen laptops (the ZenBook Duo) and multi-monitor desks:
/// every popped-out panel is a real window the window manager can place, not a
/// floating div that vanishes when the app loses focus.
///
/// All windows talk to the SAME engine on 127.0.0.1:8765, so a level shown in
/// a detached window is the identical number the main board is showing. Two
/// windows disagreeing about spot would be worse than having one.
#[tauri::command]
async fn open_panel(
    app: tauri::AppHandle,
    panel: String,
    monitor: Option<usize>,
) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder};

    // One window per panel: asking twice focuses the existing one rather than
    // stacking duplicates that then drift out of sync visually.
    let label = format!("panel-{panel}");
    if let Some(w) = app.get_webview_window(&label) {
        let _ = w.set_focus();
        return Ok(label);
    }

    let url = WebviewUrl::App(format!("index.html?panel={panel}").into());
    let mut b = WebviewWindowBuilder::new(&app, &label, url)
        .title(format!("NYAM — {panel}"))
        .inner_size(900.0, 640.0)
        .min_inner_size(420.0, 320.0)
        .theme(Some(tauri::Theme::Dark));

    // Place it on the requested monitor. On the Duo the second screen is the
    // lower panel, which is where a scanner or news rail wants to live while
    // the board stays up top.
    if let Some(idx) = monitor {
        if let Ok(monitors) = app.available_monitors() {
            if let Some(m) = monitors.get(idx) {
                let pos = m.position();
                let size = m.size();
                // Inset slightly so the window is obviously on that screen and
                // not straddling the seam between the two panels.
                b = b.position(pos.x as f64 + 40.0, pos.y as f64 + 40.0)
                    .inner_size(
                        (size.width as f64 / m.scale_factor() - 80.0).max(420.0),
                        (size.height as f64 / m.scale_factor() - 120.0).max(320.0),
                    );
            }
        }
    }

    b.build().map_err(|e| e.to_string())?;
    Ok(label)
}

/// Monitors available for placement, so the UI can offer real choices rather
/// than assuming a second screen exists.
#[tauri::command]
fn list_monitors(app: tauri::AppHandle) -> Result<Vec<serde_json::Value>, String> {
    let monitors = app.available_monitors().map_err(|e| e.to_string())?;
    let primary = app.primary_monitor().ok().flatten();
    Ok(monitors
        .iter()
        .enumerate()
        .map(|(i, m)| {
            let s = m.size();
            let p = m.position();
            let is_primary = primary
                .as_ref()
                .map(|pm| pm.position() == m.position())
                .unwrap_or(i == 0);
            serde_json::json!({
                "index": i,
                "name": m.name().cloned().unwrap_or_else(|| format!("Display {}", i + 1)),
                "width": s.width,
                "height": s.height,
                "x": p.x,
                "y": p.y,
                "scale": m.scale_factor(),
                "primary": is_primary,
            })
        })
        .collect())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![open_panel, list_monitors])
        .manage(Engine(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let child = spawn_engine(&handle);
            *app.state::<Engine>().0.lock().unwrap() = child;

            // Report the DPI scale. The CSS viewport is physical/scale, which
            // is what the layout breakpoints actually see — on a high-DPI or
            // scaled display a 1500px window can lay out as if it were ~850px,
            // and guessing at that from a screenshot is how you tune a
            // breakpoint against the wrong number.
            if let Some(w) = app.get_webview_window("main") {
                if let (Ok(scale), Ok(size)) = (w.scale_factor(), w.inner_size()) {
                    println!(
                        "[shell] dpi scale {:.2} · physical {}x{} · css ~{:.0}x{:.0}",
                        scale,
                        size.width,
                        size.height,
                        size.width as f64 / scale,
                        size.height as f64 / scale
                    );
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Kill the engine on exit. Without this the Python process
            // outlives the window and holds the port.
            // Only tear the engine down when the LAST window closes. Killing it
            // when any window exits would take the engine out from under a
            // detached panel that is still open on the second screen.
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<Engine>().0.lock().unwrap().take() {
                    kill_engine(child);
                }
            }
        });
}
