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

/// Where the engine lives relative to the running binary.
///
/// In `tauri dev` the binary sits in `src-tauri/target/debug`, so the engine is
/// four levels up. A packaged build ships the engine as a bundled resource
/// instead; that path is resolved separately once packaging is wired.
fn engine_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    // Dev: walk up from the executable to the project root.
    if let Ok(exe) = std::env::current_exe() {
        for up in [3usize, 4, 5] {
            let mut p = exe.clone();
            for _ in 0..up {
                p.pop();
            }
            let candidate = p.join("engine");
            if candidate.join("server.py").exists() {
                return Some(candidate);
            }
        }
    }
    // Packaged: engine shipped as a resource directory.
    app.path()
        .resource_dir()
        .ok()
        .map(|r| r.join("engine"))
        .filter(|p| p.join("server.py").exists())
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
    let dir = match engine_dir(app) {
        Some(d) => d,
        None => {
            // Not fatal. The frontend polls /api/health and shows an explicit
            // "engine unreachable" screen, which is more useful than a panic
            // and lets a manually started engine still be picked up.
            eprintln!("[shell] engine directory not found; expecting a manually started engine");
            return None;
        }
    };

    let python = resolve_python();
    match Command::new(&python)
        .arg("server.py")
        .arg("--port")
        .arg(ENGINE_PORT.to_string())
        .current_dir(&dir)
        .spawn()
    {
        Ok(child) => {
            println!("[shell] engine pid {} ({}) in {}", child.id(), python, dir.display());
            Some(child)
        }
        Err(e) => {
            eprintln!("[shell] failed to spawn engine ({python} server.py): {e}");
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
