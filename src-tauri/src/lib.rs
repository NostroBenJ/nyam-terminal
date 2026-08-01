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

/// Locate the engine, preferring a bundled binary over the source tree.
///
/// Order matters. A packaged install must never silently fall through to a
/// developer's checkout — it would run different code than was shipped, and
/// the difference would only show up as numbers that don't match.
fn find_engine(app: &tauri::AppHandle) -> Option<EngineKind> {
    let exe_name = if cfg!(windows) { "nyam-engine.exe" } else { "nyam-engine" };

    // 1. Packaged: bundled as a Tauri resource.
    if let Ok(res) = app.path().resource_dir() {
        let dir = res.join("engine");
        if dir.join(exe_name).exists() {
            return Some(EngineKind::Bundled(dir));
        }
    }

    // 2. Dev: walk up from the executable to the project root.
    if let Ok(exe) = std::env::current_exe() {
        for up in [3usize, 4, 5] {
            let mut p = exe.clone();
            for _ in 0..up {
                p.pop();
            }
            let engine = p.join("engine");
            // A locally built binary still beats invoking Python.
            let built = engine.join("dist").join("nyam-engine");
            if built.join(exe_name).exists() {
                return Some(EngineKind::Bundled(built));
            }
            if engine.join("server.py").exists() {
                return Some(EngineKind::Source(engine));
            }
        }
    }
    None
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
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
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<Engine>().0.lock().unwrap().take() {
                    kill_engine(child);
                }
            }
        });
}
