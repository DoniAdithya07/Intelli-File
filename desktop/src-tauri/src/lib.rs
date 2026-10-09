use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
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

/// Why the search engine is not running, in sentences the UI can show (port
/// taken, start failed, crashed). None while all is well.
struct BackendProblem(Mutex<Option<String>>);

/// Whether Ctrl+Space was registered; the sidebar hides its hint when not.
struct ShortcutOk(AtomicBool);

fn new_token() -> String {
    // 128 bits straight from the operating system's random generator.
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("the operating system has no random source");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// Only plain documents and media are opened from the app. An allowlist, not a
// blocklist: the list of things Windows can run (exe, lnk, appref-ms, chm, py,
// ...) is never complete. html/htm/svg/xhtml are left out on purpose: the
// default browser would run their script with access to local files. So is
// xml: an XHTML- or SVG-namespaced .xml file runs script the same way.
const ALLOWED_EXTENSIONS: &[&str] = &[
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt", "md", "csv", "tsv", "json",
    "log", "epub", "eml", "msg", "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif", "mp3",
    "wav", "m4a", "flac", "ogg", "aiff", "aif", "wma", "aac", "opus", "avif", "mp4", "mov", "m4v", "avi", "mkv", "webm",
    "3gp", "wmv", "mts", "m2ts", "mpg", "mpeg",
];

/// True only for a path the backend reads back as the very same path (it
/// strips whitespace and folds `..`, `.` and repeated separators, so anything
/// that would fold is refused) and whose file type is on the allowlist.
fn is_openable_path(path: &str) -> bool {
    if path.is_empty() || path != path.trim() || path.contains('\0') {
        return false;
    }
    let parts: Vec<&str> = path.split(['\\', '/']).collect();
    // Empty parts: trailing or doubled separator, i.e. no file name. The one
    // allowed empty part is the first, from a leading "/" on macOS/Linux.
    if parts.len() < 2 || parts[1..].iter().any(|p| p.is_empty()) || parts.iter().any(|p| *p == "." || *p == "..") {
        return false;
    }
    let name = parts[parts.len() - 1];
    // ':' in the file name is an NTFS alternate data stream ("a.txt:evil.exe").
    if name.contains(':') {
        return false;
    }
    // Windows ignores trailing dots and spaces, so "a.exe. " still runs as a.exe.
    name.trim_end_matches(['.', ' '])
        .rsplit_once('.')
        .is_some_and(|(_, e)| ALLOWED_EXTENSIONS.contains(&e.to_ascii_lowercase().as_str()))
}

fn percent_encode(s: &str) -> String {
    s.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

const MAX_RESPONSE_BYTES: u64 = 64 * 1024;

/// Asks the local backend whether the user let IntelliFile index this file.
/// Plain HTTP to 127.0.0.1 only, so no HTTP client crate is needed.
///
/// The token goes to whatever listens on the port. That is the backend this
/// app started itself (it binds 8756 first, at launch); anything squatting the
/// port would only learn a per-launch token that dies with the app.
fn is_indexed(path: &str, token: &str) -> bool {
    is_indexed_at(BACKEND_PORT, path, token, std::time::Duration::from_secs(5))
}

fn is_indexed_at(port: u16, path: &str, token: &str, timeout: std::time::Duration) -> bool {
    use std::io::Read;
    let Ok(mut stream) = std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), timeout) else {
        return false;
    };
    if stream.set_read_timeout(Some(timeout)).is_err() || stream.set_write_timeout(Some(timeout)).is_err() {
        return false;
    }
    let request = format!(
        "GET /is-indexed?path={} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-IntelliFile-Token: {token}\r\nConnection: close\r\n\r\n",
        percent_encode(path)
    );
    let mut raw = Vec::new();
    if stream.write_all(request.as_bytes()).is_err() || stream.take(MAX_RESPONSE_BYTES).read_to_end(&mut raw).is_err() {
        return false;
    }
    parse_is_indexed_response(&raw)
}

/// A 200 status line, then a body of exactly {"indexed": <bool>} (the shape of
/// the backend's /is-indexed route). Anything else counts as "not indexed".
fn parse_is_indexed_response(raw: &[u8]) -> bool {
    let Some(split) = raw.windows(4).position(|w| w == b"\r\n\r\n") else { return false };
    let Ok(head) = std::str::from_utf8(&raw[..split]) else { return false };
    let mut status = head.lines().next().unwrap_or("").split(' ');
    if !status.next().is_some_and(|v| v.starts_with("HTTP/1.")) || status.next() != Some("200") {
        return false;
    }
    match serde_json::from_slice::<serde_json::Value>(&raw[split + 4..]) {
        Ok(serde_json::Value::Object(o)) => o.len() == 1 && o.get("indexed").and_then(|v| v.as_bool()) == Some(true),
        _ => false,
    }
}

/// Opens a file with its default app, but only a file the user let IntelliFile
/// index and only if it is a plain document or media type. Replaces the blanket
/// opener:allow-open-path permission, which let the web view open any path.
#[tauri::command]
async fn open_indexed_path(app: tauri::AppHandle, state: tauri::State<'_, ApiToken>, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    if !is_openable_path(&path) {
        return Err("IntelliFile only opens documents, images, audio and video.".into());
    }
    // Port 8756 belongs to some other program: never hand it the token.
    if app.state::<BackendProblem>().0.lock().unwrap().as_deref() == Some(PORT_TAKEN) {
        return Err(PORT_TAKEN.into());
    }
    // The check is a blocking TCP call: run it on a worker thread, not the main one.
    let token = state.0.clone();
    let check = path.clone();
    let indexed = tauri::async_runtime::spawn_blocking(move || is_indexed(&check, &token))
        .await
        .map_err(|e| e.to_string())?;
    if !indexed {
        return Err("That file is not in the index.".into());
    }
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn api_token(state: tauri::State<ApiToken>, problem: tauri::State<BackendProblem>) -> String {
    // With the port held by another program, the web view sends it no token.
    if problem.0.lock().unwrap().as_deref() == Some(PORT_TAKEN) {
        return String::new();
    }
    state.0.clone()
}

#[tauri::command]
fn backend_problem(state: tauri::State<BackendProblem>) -> Option<String> {
    state.0.lock().unwrap().clone()
}

#[tauri::command]
fn shortcut_ok(state: tauri::State<ShortcutOk>) -> bool {
    state.0.load(Ordering::Relaxed)
}

const PORT_TAKEN: &str = "Port 8756 is used by another program. Close it (or restart Windows) and reopen IntelliFile.";

fn set_problem(app: &tauri::AppHandle, problem: String) {
    shell_log(app, &problem);
    *app.state::<BackendProblem>().0.lock().unwrap() = Some(problem);
}

/// The last few non-empty lines of backend-console.log, read from its last 4 KB.
fn console_tail(app: &tauri::AppHandle, lines: usize) -> String {
    use std::io::{Read, Seek, SeekFrom};
    let Some(mut f) = logs_dir(app).and_then(|d| std::fs::File::open(d.join("backend-console.log")).ok()) else {
        return String::new();
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let mut buf = Vec::new();
    if f.seek(SeekFrom::Start(len.saturating_sub(4096))).is_err() || f.read_to_end(&mut buf).is_err() {
        return String::new();
    }
    last_lines(&String::from_utf8_lossy(&buf), lines)
}

fn last_lines(text: &str, n: usize) -> String {
    let kept: Vec<&str> = text.lines().map(str::trim_end).filter(|l| !l.is_empty()).collect();
    kept[kept.len().saturating_sub(n)..].join("\n")
}

/// Watches the spawned backend and records why when it exits on its own.
/// RunEvent::Exit takes the child out of the mutex first, so a deliberate
/// shutdown is never reported as a crash.
fn watch_backend(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let state = app.state::<Backend>();
        let mut guard = state.0.lock().unwrap();
        let Some(child) = guard.as_mut() else { return };
        match child.try_wait() {
            Ok(None) => {}
            Ok(Some(status)) => {
                guard.take();
                drop(guard);
                let code = status.code().map_or_else(|| "unknown".to_string(), |c| c.to_string());
                let mut problem = format!("The search engine stopped (exit code {code}).");
                let tail = console_tail(&app, 5);
                if !tail.is_empty() {
                    problem.push_str(" Last lines of backend-console.log:\n");
                    problem.push_str(&tail);
                }
                set_problem(&app, problem);
                return;
            }
            Err(e) => {
                shell_log(&app, &format!("could not watch the backend: {e}"));
                return;
            }
        }
    });
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
    // A program already listening on 8756 would get every request and the
    // token. Do not start, and say why.
    match std::net::TcpListener::bind(("127.0.0.1", BACKEND_PORT)) {
        Ok(probe) => drop(probe),
        Err(e) => {
            shell_log(app, &format!("port {BACKEND_PORT} is taken: {e}"));
            set_problem(app, PORT_TAKEN.to_string());
            return None;
        }
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
        // Both streams: tracebacks go to stderr.
        Some(f) => match f.try_clone() {
            Ok(dup) => (Stdio::from(dup), Stdio::from(f)),
            Err(_) => (Stdio::from(f), Stdio::inherit()),
        },
        None => (Stdio::inherit(), Stdio::inherit()),
    };
    let mut cmd = Command::new(&exe);
    cmd.arg("--port")
        .arg(BACKEND_PORT.to_string())
        .env("INTELLIFILE_MODELS_DIR", resources.join("models"))
        .env("INTELLIFILE_DATA_DIR", resources.join("data"))
        .env("INTELLIFILE_API_TOKEN", token)
        // The backend exits on its own when this process is gone (a crash or
        // a force-close never reaches the kill in RunEvent::Exit).
        .env("INTELLIFILE_PARENT_PID", std::process::id().to_string())
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
            set_problem(app, format!("The search engine could not be started ({e}). Antivirus software may have blocked it; extract IntelliFile-windows.zip again."));
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // A dev shell can share the token of a backend started by hand.
    #[cfg(debug_assertions)]
    let token = std::env::var("INTELLIFILE_API_TOKEN").ok().filter(|t| !t.is_empty()).unwrap_or_else(new_token);
    #[cfg(not(debug_assertions))]
    let token = new_token();
    let app = tauri::Builder::default()
        // Must be the first plugin: a second launch hands over to this one.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(main) = app.get_webview_window("main") {
                let _ = main.unminimize();
                let _ = main.show();
                let _ = main.set_focus();
            }
        }))
        .manage(Backend(Mutex::new(None)))
        .manage(ApiToken(token.clone()))
        .manage(BackendProblem(Mutex::new(None)))
        .manage(ShortcutOk(AtomicBool::new(false)))
        .invoke_handler(tauri::generate_handler![api_token, open_indexed_path, backend_problem, shortcut_ok])
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
        .on_window_event(|window, event| {
            // The hidden Ctrl+Space overlay is a window too, so closing the
            // main window alone never ended the app: on Windows it lived on
            // invisibly (no taskbar entry, no tray) with its backend and the
            // global shortcut. Closing the main window quits.
            match (window.label(), event) {
                ("main", tauri::WindowEvent::CloseRequested { .. }) => window.app_handle().exit(0),
                // Alt+F4 on the overlay only hides it: a destroyed overlay
                // never comes back on Ctrl+Space. Clicking away hides it too.
                ("overlay", tauri::WindowEvent::CloseRequested { api, .. }) => {
                    api.prevent_close();
                    let _ = window.hide();
                }
                ("overlay", tauri::WindowEvent::Focused(false)) => {
                    let _ = window.hide();
                }
                _ => {}
            }
        })
        .setup(|app| {
            use tauri_plugin_global_shortcut::GlobalShortcutExt;
            match app.global_shortcut().register(OVERLAY_SHORTCUT) {
                Ok(()) => app.state::<ShortcutOk>().0.store(true, Ordering::Relaxed),
                Err(e) => shell_log(app.handle(), &format!("could not register {OVERLAY_SHORTCUT} (another program may use it): {e}")),
            }
            let token = app.state::<ApiToken>().0.clone();
            let child = spawn_backend(app.handle(), &token);
            let spawned = child.is_some();
            *app.state::<Backend>().0.lock().unwrap() = child;
            if spawned {
                watch_backend(app.handle().clone());
            }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_allowlisted_types_with_unfolded_paths_are_openable() {
        for p in [
            "C:\\x\\a.exe", "C:\\x\\a.EXE.", "C:\\x\\a.exe::$DATA", "C:\\x\\a.txt:evil.exe", "C:\\x\\dir\\", "C:\\x\\a.appref-ms",
            "C:\\x\\a.chm", "C:\\x\\a.py", "C:\\x\\a.lnk", "C:\\x\\a.html", "C:\\x\\a.svg", "C:\\x\\README",
            "C:\\x\\..\\a.docx", "C:\\x\\.\\a.docx", "C:\\x\\\\a.docx", " C:\\x\\a.docx", "C:\\x\\a.docx ", "", "/a/run.ps1",
            "a.docx", "C:\\x\\a.docx\0.exe",
        ] {
            assert!(!is_openable_path(p), "{p:?}");
        }
        for p in ["C:\\x\\a.docx", "C:\\x\\A.PDF", "C:\\x\\a.docx.", "/home/u/photo.jpg", "C:\\my.exe folder\\notes.txt", "C:/x/a.mp4"] {
            assert!(is_openable_path(p), "{p:?}");
        }
    }

    /// One-shot fake backend: reads the request, sends `reply`, keeps the socket
    /// open for `hold`, then returns what it read.
    fn fake_backend(reply: Vec<u8>, hold: std::time::Duration) -> (u16, std::thread::JoinHandle<String>) {
        use std::io::Read;
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut got = Vec::new();
            let mut buf = [0u8; 512];
            while !got.windows(4).any(|w| w == b"\r\n\r\n") {
                match s.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => got.extend_from_slice(&buf[..n]),
                }
            }
            let _ = s.write_all(&reply);
            std::thread::sleep(hold);
            String::from_utf8_lossy(&got).into_owned()
        });
        (port, handle)
    }

    const T: std::time::Duration = std::time::Duration::from_secs(2);
    const NO_HOLD: std::time::Duration = std::time::Duration::ZERO;

    #[test]
    fn is_indexed_true_on_a_uvicorn_style_crlf_reply_and_sends_a_proper_request() {
        let reply = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 16\r\n\r\n{\"indexed\":true}";
        let (port, server) = fake_backend(reply.to_vec(), NO_HOLD);
        assert!(is_indexed_at(port, "C:\\a b\\x.txt", "tok123", T));
        let req = server.join().unwrap();
        assert!(req.starts_with("GET /is-indexed?path=C%3A%5Ca%20b%5Cx.txt HTTP/1.1\r\nHost: 127.0.0.1:"), "{req:?}");
        assert!(req.contains("\r\nX-IntelliFile-Token: tok123\r\n"), "{req:?}");
        assert!(req.contains("\r\nConnection: close\r\n\r\n"), "{req:?}");
    }

    #[test]
    fn is_indexed_false_for_false_body_bad_status_garbage_and_wrong_shape() {
        let cases: [&[u8]; 7] = [
            b"HTTP/1.1 200 OK\r\ncontent-length: 17\r\n\r\n{\"indexed\":false}",
            b"HTTP/1.1 401 Unauthorized\r\n\r\n{\"indexed\":true}",
            b"HTTP/1.1 500 Oops\r\n\r\n{\"indexed\":true}",
            b"garbage garbage garbage",
            b"HTTP/1.1 200 OK\n\n{\"indexed\":true}",
            b"HTTP/1.1 200 OK\r\n\r\n{\"indexed\":true,\"extra\":1}",
            b"HTTP/1.1 200 OK\r\n\r\n{\"indexed\":\"true\"}",
        ];
        for reply in cases {
            let (port, server) = fake_backend(reply.to_vec(), NO_HOLD);
            assert!(!is_indexed_at(port, "C:\\x.txt", "t", T), "{:?}", String::from_utf8_lossy(reply));
            server.join().unwrap();
        }
    }

    #[test]
    fn is_indexed_false_when_the_server_never_answers_or_is_absent() {
        let (port, server) = fake_backend(Vec::new(), std::time::Duration::from_millis(1500));
        let start = std::time::Instant::now();
        assert!(!is_indexed_at(port, "C:\\x.txt", "t", std::time::Duration::from_millis(300)));
        assert!(start.elapsed() < std::time::Duration::from_millis(1200), "did not time out");
        server.join().unwrap();
        assert!(!is_indexed_at(port, "C:\\x.txt", "t", T), "nothing listens any more");
    }

    #[test]
    fn is_indexed_reads_at_most_64kb() {
        let mut big = b"HTTP/1.1 200 OK\r\n\r\n".to_vec();
        big.extend(std::iter::repeat(b' ').take(70_000));
        big.extend_from_slice(b"{\"indexed\":true}");
        let (port, server) = fake_backend(big, NO_HOLD);
        assert!(!is_indexed_at(port, "C:\\x.txt", "t", T));
        let _ = server.join();
    }

    /// Against the real backend: INTELLIFILE_TEST_PORT / _TOKEN / _INDEXED / _UNINDEXED.
    #[test]
    #[ignore = "needs a running backend, see the env vars"]
    fn real_backend_says_true_for_indexed_and_false_for_unindexed() {
        let var = |k: &str| std::env::var(k).unwrap_or_else(|_| panic!("set {k}"));
        let port: u16 = var("INTELLIFILE_TEST_PORT").parse().unwrap();
        let token = var("INTELLIFILE_TEST_TOKEN");
        assert!(is_indexed_at(port, &var("INTELLIFILE_TEST_INDEXED"), &token, T));
        assert!(!is_indexed_at(port, &var("INTELLIFILE_TEST_UNINDEXED"), &token, T));
        assert!(!is_indexed_at(port, &var("INTELLIFILE_TEST_INDEXED"), "wrong-token", T));
    }

    #[test]
    fn token_is_32_hex_chars_and_differs_each_time() {
        let (a, b) = (new_token(), new_token());
        assert_eq!(a.len(), 32);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn last_lines_keeps_the_last_non_empty_lines() {
        assert_eq!(last_lines("a\r\n\r\nb\nc\n  \nd\ne\nf\n", 5), "b\nc\nd\ne\nf");
        assert_eq!(last_lines("only\n", 5), "only");
        assert_eq!(last_lines("", 5), "");
    }

    #[test]
    fn new_media_types_are_openable() {
        for p in ["C:\\x\\a.aiff", "C:\\x\\a.AIF", "C:\\x\\a.avif", "C:\\x\\a.m4v"] {
            assert!(is_openable_path(p), "{p:?}");
        }
    }

    #[test]
    fn types_indexed_since_october_are_openable_and_script_hosts_still_are_not() {
        for ext in ["eml", "msg", "tif", "HEIF", "wma", "aac", "opus", "3gp", "wmv", "mts", "m2ts", "mpg", "mpeg"] {
            let p = format!("C:\\x\\a.{ext}");
            assert!(is_openable_path(&p), "{p:?}");
        }
        // .xml too: an XHTML- or SVG-namespaced .xml file runs script in the default browser.
        for p in ["C:\\x\\a.ts", "C:\\x\\a.xhtml", "C:\\x\\a.hta", "C:\\x\\a.js", "C:\\x\\a.vbs", "C:\\x\\a.msi", "C:\\x\\a.xml", "C:\\x\\a.XML"] {
            assert!(!is_openable_path(p), "{p:?}");
        }
    }

    #[test]
    fn percent_encoding_keeps_unreserved_and_escapes_the_rest() {
        assert_eq!(percent_encode("C:\\a b/é.txt"), "C%3A%5Ca%20b%2F%C3%A9.txt");
    }
}
