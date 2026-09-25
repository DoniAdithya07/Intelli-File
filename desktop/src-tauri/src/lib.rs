use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Emitter, Manager, RunEvent};

// Ctrl+Space (Cmd+Space is taken by Spotlight on macOS, so Ctrl on both OSes)
// toggles the search overlay window from anywhere on the desktop.
const OVERLAY_SHORTCUT: &str = "Control+Space";
const BACKEND_PORT: u16 = 8756;

/// The Python backend, started by the shell (Phase 14). In development the
/// developer runs uvicorn by hand and nothing is spawned; the frontend polls
/// /health either way. A packaged app has no Python on the machine, so it
/// ships the PyInstaller bundle under Resources/backend and starts it here,
/// pointed at the bundled models/data, and stops it when the window closes.
struct Backend(Mutex<Option<Child>>);

/// Per-launch secret the backend requires on every request (Phase 12).
/// Only this process and the backend it spawned ever know it.
struct ApiToken(String);

fn new_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    // Two independently seeded SipHash states give 128 bits of OS-provided
    // randomness without pulling in a crate.
    let mut out = String::new();
    for _ in 0..2 {
        let mut h = RandomState::new().build_hasher();
        h.write_u64(std::process::id() as u64);
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out
}

#[tauri::command]
fn api_token(state: tauri::State<ApiToken>) -> String {
    state.0.clone()
}

/// <local data>/IntelliFile/logs — the same folder the backend logs into
/// (~/Library/Application Support/IntelliFile on macOS, %LOCALAPPDATA%\IntelliFile on Windows).
fn logs_dir(app: &tauri::AppHandle) -> Option<PathBuf> {
    let base = app.path().local_data_dir().ok()?;
    let dir = base.join("IntelliFile").join("logs");
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

/// A GUI app has no terminal: every shell-side event goes to shell.log so
/// "the engine never came up" on someone else's machine can be diagnosed.
fn shell_log(app: &tauri::AppHandle, line: &str) {
    eprintln!("[intellifile] {line}");
    if let Some(dir) = logs_dir(app) {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("shell.log")) {
            let ts = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            let _ = writeln!(f, "{ts} {line}");
        }
    }
}

fn spawn_backend(app: &tauri::AppHandle, token: &str) -> Option<Child> {
    let resources = match app.path().resource_dir() {
        Ok(r) => r,
        Err(e) => {
            shell_log(app, &format!("could not resolve the resource directory: {e}"));
            return None;
        }
    };
    let exe_name = if cfg!(windows) { "intellifile-backend.exe" } else { "intellifile-backend" };
    let exe = resources.join("backend").join(exe_name);
    if !exe.exists() {
        shell_log(app, &format!("no bundled backend at {} — assuming a dev server on port {}", exe.display(), BACKEND_PORT));
        return None;
    }
    #[cfg(unix)]
    {
        // Resource copying does not always keep the executable bit.
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(&exe) {
            let mut perms = meta.permissions();
            perms.set_mode(perms.mode() | 0o755);
            let _ = std::fs::set_permissions(&exe, perms);
        }
    }
    // The backend's console output (uvicorn, tracebacks) goes to a file too.
    let (out, err) = match logs_dir(app).and_then(|d| std::fs::OpenOptions::new().create(true).append(true).open(d.join("backend-console.log")).ok()) {
        Some(f) => (Stdio::from(f.try_clone().unwrap_or(f)), Stdio::inherit()),
        None => (Stdio::inherit(), Stdio::inherit()),
    };
    let mut cmd = Command::new(&exe);
    cmd.arg("--port")
        .arg(BACKEND_PORT.to_string())
        .env("INTELLIFILE_MODELS_DIR", resources.join("models"))
        .env("INTELLIFILE_DATA_DIR", resources.join("data"))
        .env("INTELLIFILE_API_TOKEN", token)
        .current_dir(exe.parent().unwrap_or(&resources))
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up behind the app
    }
    match cmd.spawn() {
        Ok(child) => {
            shell_log(app, &format!("backend started from {} (pid {}) on port {}", exe.display(), child.id(), BACKEND_PORT));
            Some(child)
        }
        Err(e) => {
            shell_log(app, &format!("could not start the bundled backend {}: {e} (is the file executable? quarantined?)", exe.display()));
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let token = new_token();
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .manage(ApiToken(token.clone()))
        .invoke_handler(tauri::generate_handler![api_token])
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    use tauri_plugin_global_shortcut::ShortcutState;
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    if !shortcut.matches(tauri_plugin_global_shortcut::Modifiers::CONTROL, tauri_plugin_global_shortcut::Code::Space) {
                        return;
                    }
                    if let Some(overlay) = app.get_webview_window("overlay") {
                        let visible = overlay.is_visible().unwrap_or(false);
                        if visible {
                            let _ = overlay.hide();
                        } else {
                            let _ = overlay.center();
                            let _ = overlay.show();
                            let _ = overlay.set_focus();
                            // Tell the overlay page to reset + focus its input.
                            let _ = overlay.emit("overlay-shown", ());
                        }
                    }
                })
                .build(),
        )
        .setup(|app| {
            use tauri_plugin_global_shortcut::GlobalShortcutExt;
            if let Err(e) = app.global_shortcut().register(OVERLAY_SHORTCUT) {
                eprintln!("could not register {OVERLAY_SHORTCUT}: {e}");
            }
            let token = app.state::<ApiToken>().0.clone();
            let child = spawn_backend(app.handle(), &token);
            *app.state::<Backend>().0.lock().unwrap() = child;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            // Take the backend down with the window; a stray server on
            // port 8756 would block the next launch.
            if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
