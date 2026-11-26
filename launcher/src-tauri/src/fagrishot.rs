// SPDX-License-Identifier: GPL-3.0-or-later
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod cpu;
mod direct_play;

use std::io::{BufRead, BufReader, Write};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_dialog::DialogExt;

struct ActiveTask {
    pid: u32,
    cancellable: bool,
    cancelled: bool,
}

#[derive(Default)]
struct Task(Mutex<Option<ActiveTask>>);

fn source() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let folder = exe.parent().ok_or("Could not locate the launcher folder.")?;
    if folder.join("player-package.json").is_file() {
        return Ok(folder.to_owned());
    }
    // The built game can open its sibling launcher in the user's persistent game directory.
    if let Some(local) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        let managed = local.join("FagriShot/build/game");
        if local.is_absolute() && managed.is_dir() && managed.canonicalize().ok().is_some_and(|p| folder.canonicalize().ok().as_ref() == Some(&p)) {
            return managed_source(folder, &exe);
        }
    }
    let mut checkout = folder;
    if folder.ends_with("work/tools") {
        checkout = folder.parent().and_then(Path::parent).unwrap_or(folder);
    }
    if checkout.join("recomp/games/fefw/config.py").is_file() && checkout.join("scripts/fagrishot.py").is_file() {
        return Ok(checkout.to_owned());
    }
    Err("Could not find player-package.json beside the launcher. Download FagriShot-Windows-x64.zip, use Extract All, and open FagriShotLauncher.exe from the extracted folder.".into())
}

fn managed_source(folder: &Path, exe: &Path) -> Result<PathBuf, String> {
    let repair = "The saved player location is missing or has changed. Open FagriShotLauncher.exe from the extracted player folder to reconnect the built game.";
    let recorded = std::fs::read_to_string(folder.join("player-root.txt")).map_err(|_| repair)?;
    let root = PathBuf::from(recorded.trim_end_matches(['\r', '\n']));
    if !root.is_absolute() || !root.join("player-package.json").is_file() { return Err(repair.into()); }
    let installed = root.join("FagriShotLauncher.exe");
    let len = std::fs::metadata(exe).map_err(|_| repair)?.len();
    if std::fs::metadata(&installed).map_err(|_| repair)?.len() != len ||
        std::fs::read(installed).map_err(|_| repair)? != std::fs::read(exe).map_err(|_| repair)? {
        return Err(repair.into());
    }
    Ok(root)
}

fn command(args: &[String]) -> Result<Command, String> {
    let root = source()?;
    let local = root.join("tools/.venv/Scripts/python.exe");
    let bundled = root.join("tools/python/python.exe");
    let python = if root.join("player-package.json").is_file() || bundled.is_file() {
        for file in ["tools/python/python.exe", "tools/python/python313.dll", "tools/python/python313.zip",
                     "tools/python/python313._pth", "tools/python/vcruntime140.dll", "tools/python/vcruntime140_1.dll",
                     "player-package.json", "scripts/fagrishot.py"] {
            if !root.join(file).is_file() {
                return Err(format!("The player installation is incomplete: missing {file}. Python is included in FagriShot-Windows-x64.zip. Use Extract All on the entire ZIP and open the launcher in the extracted folder."));
            }
        }
        bundled
    } else if local.is_file() {
        local
    } else {
        PathBuf::from("python")
    };
    let mut cmd = Command::new(python);
    // -I excludes user-controlled Python imports. Run only the adjacent project through its explicit entry point.
    cmd.current_dir(&root).args(["-I", "-X", "utf8", "-u"]).arg(root.join("scripts/fagrishot.py")).args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    Ok(cmd)
}

#[tauri::command]
async fn pick(app: tauri::AppHandle, kind: String) -> Option<String> {
    if kind != "ExeFS" && kind != "RomFS" { return None; }
    app.dialog().file().set_title(format!("Select extracted {kind}")).blocking_pick_folder()
        .and_then(|p| p.into_path().ok()).map(|p| p.to_string_lossy().into_owned())
}

async fn json_command(args: Vec<String>) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let output = command(&args)?.output().map_err(|e| format!("Could not start FagriShot's local tools: {e}"))?;
        if !output.status.success() {
            let diagnostic = String::from_utf8_lossy(&output.stderr);
            return Err(if diagnostic.trim().is_empty() {
                format!("The local tools exited with {}. Extract the full FagriShot Windows player ZIP, including its bundled Python and DLLs.", output.status)
            } else { diagnostic.trim().to_owned() });
        }
        serde_json::from_slice(&output.stdout).map_err(|e| format!("Could not read the response from FagriShot: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn overview() -> Result<serde_json::Value, String> {
    let mut status = json_command(vec!["status".into(), "--json".into()]).await?;
    if let Some(reason) = cpu::check() {
        status["cpu_error"] = reason.into();
    }
    Ok(status)
}

#[tauri::command]
async fn graphics(changes: BTreeMap<String, String>) -> Result<serde_json::Value, String> {
    let mut args = vec!["graphics".into()];
    for (name, value) in changes {
        if !["output", "scale", "fps", "upscaler", "reflections", "ray_reflections"].contains(&name.as_str()) {
            return Err("Unknown graphics setting.".into());
        }
        args.push(format!("--{name}={value}"));
    }
    json_command(args).await
}

#[tauri::command]
async fn arabic_text(enabled: Option<bool>) -> Result<serde_json::Value, String> {
    let args = match enabled {
        Some(value) => vec!["arabic-set".into(), "--enabled".into(), if value { "on" } else { "off" }.into()],
        None => vec!["arabic-status".into(), "--json".into()],
    };
    json_command(args).await
}

#[tauri::command]
async fn action(app: tauri::AppHandle, action: String, exefs: String, romfs: String, backup_id: String) -> Result<(), String> {
    if !["build-and-prepare","play","doctor","backup","backups","restore"].contains(&action.as_str()) {
        return Err("Unknown launcher action.".into());
    }
    let mut args = vec![action.clone()];
    if action == "restore" { args.push(backup_id); }
    if !matches!(action.as_str(), "doctor" | "backup" | "backups" | "restore") {
        if !exefs.is_empty() { args.extend(["--exefs".into(), exefs]); }
        if !romfs.is_empty() { args.extend(["--romfs".into(), romfs]); }
    }
    if matches!(action.as_str(),"build-and-prepare"|"play") {
        if let Some(reason) = cpu::check() { return Err(reason); }
    }
    tauri::async_runtime::spawn_blocking(move || {
        let mut cmd = command(&args)?;
        let state = app.state::<Task>();
        let mut child = {
            let mut job = state.0.lock().unwrap();
            if job.is_some() { return Err("Another action is running.".into()); }
            let child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|e| format!("Could not start FagriShot's local tools: {e}"))?;
            let cancellable = action == "build-and-prepare";
            *job = Some(ActiveTask { pid: child.id(), cancellable, cancelled: false });
            if cancellable { let _ = app.emit("action-cancellable", &action); }
            child
        };
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        std::thread::scope(|s| {
            let a = &app;
            s.spawn(move || for line in BufReader::new(stdout).lines().map_while(Result::ok) { let _ = a.emit("build-line", line); });
            s.spawn(move || for line in BufReader::new(stderr).lines().map_while(Result::ok) { let _ = a.emit("build-line", line); });
        });
        let result = child.wait().map_err(|e| e.to_string());
        let cancelled = state.0.lock().unwrap().take().is_some_and(|job| job.cancelled);
        if cancelled { return Err("Cancelled. Run the interrupted operation again when ready.".into()); }
        if result?.success() { Ok(()) } else { Err("The operation stopped. See the last diagnostic below.".into()) }
    }).await.map_err(|e| e.to_string())?
}

fn stop(state: &Task) -> Result<bool, String> {
    let mut job = state.0.lock().unwrap();
    if let Some(job) = job.as_mut().filter(|job| job.cancellable) {
        if job.cancelled { return Ok(true); }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let result = Command::new("taskkill").args(["/T", "/F", "/PID", &job.pid.to_string()])
                .creation_flags(0x08000000).output().map_err(|e| format!("Could not cancel the operation: {e}"))?;
            if !result.status.success() { return Err("Windows could not stop this operation.".into()); }
            job.cancelled = true;
            return Ok(true);
        }
    }
    Ok(false)
}
#[tauri::command]
async fn cancel(app: tauri::AppHandle) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || stop(&app.state::<Task>())).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn startup_play_error(error: tauri::State<'_, Option<String>>) -> Option<String> {
    error.inner().clone()
}

#[cfg(windows)]
fn require_webview(play_error: Option<&str>) {
    if std::env::var_os("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER").is_some() || tauri::webview_version().is_ok() { return; }
    use windows::core::{w, HSTRING, PCWSTR};
    use windows::Win32::UI::Shell::ShellExecuteW;
    use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, IDYES, MB_ICONERROR, MB_YESNO, SW_SHOWNORMAL};
    let page = "https://developer.microsoft.com/microsoft-edge/webview2/#download-section";
    let previous = play_error.map(|e| format!("{e}\n\n")).unwrap_or_default();
    let message = format!("{previous}Game setup needs Microsoft Edge WebView2 Runtime. Install the Evergreen Runtime for Windows x64, then reopen FagriShot.\n\n{page}\n\nOpen Microsoft's download page?");
    unsafe {
        if MessageBoxW(None, &HSTRING::from(message), w!("FagriShot setup"), MB_YESNO | MB_ICONERROR) == IDYES {
            let _ = ShellExecuteW(None, w!("open"), &HSTRING::from(page), PCWSTR::null(), PCWSTR::null(), SW_SHOWNORMAL);
        }
    }
    std::process::exit(2);
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let play_error = if args == ["--play-game"] {
        match direct_play::run() {
            Ok(()) => return,
            Err(error) => Some(error),
        }
    } else if !args.is_empty() {
        let result = command(&args).and_then(|mut cmd| cmd.status().map_err(|e| e.to_string()));
        match result {
            Ok(status) => std::process::exit(status.code().unwrap_or(1)),
            Err(e) => { let _ = writeln!(std::io::stderr(), "{e}"); std::process::exit(2); }
        }
    } else {
        None
    };
    #[cfg(windows)]
    require_webview(play_error.as_deref());
    let startup = tauri::Builder::default().plugin(tauri_plugin_dialog::init()).manage(Task::default()).manage(play_error.clone())
        .on_window_event(|win, ev| {
            if matches!(ev, tauri::WindowEvent::Destroyed) { let _ = stop(&win.state::<Task>()); }
        })
        .invoke_handler(tauri::generate_handler![overview, graphics, arabic_text, pick, action, cancel, startup_play_error])
        .run(tauri::generate_context!());
    if let Err(error) = startup {
        let detail = format!("{}Could not open Game setup. Check that Microsoft Edge WebView2 Runtime is working and your Windows profile is writable.\n{error}\n\nWebView2 help: https://developer.microsoft.com/microsoft-edge/webview2/",
            play_error.map(|e| format!("{e}\n\n")).unwrap_or_default());
        let _ = writeln!(std::io::stderr(), "{detail}");
        #[cfg(windows)]
        unsafe {
            use windows::core::{w, HSTRING};
            use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
            let _ = MessageBoxW(None, &HSTRING::from(detail), w!("FagriShot"), MB_OK | MB_ICONERROR);
        }
        std::process::exit(2);
    }
}
