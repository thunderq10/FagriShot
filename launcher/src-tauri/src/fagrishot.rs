// SPDX-License-Identifier: GPL-3.0-or-later
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod cpu;
mod direct_play;

use std::io::{BufRead, BufReader, Write};
use std::collections::BTreeMap;
use std::path::PathBuf;
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
    exe.ancestors().skip(1).find(|p| p.join("player-package.json").is_file() ||
        (p.join("fagrishot/__main__.py").is_file() && p.join("compatibility/supported.json").is_file()))
        .map(|p| p.to_owned()).ok_or_else(|| "Extract the entire player ZIP into one folder, then open FagriShotLauncher.exe there.".into())
}

fn command(args: &[String]) -> Result<Command, String> {
    let root = source()?;
    let local = root.join("tools/.venv/Scripts/python.exe");
    let bundled = root.join("tools/python/python.exe");
    let python = if root.join("player-package.json").is_file() || bundled.is_file() {
        if !bundled.is_file() || !root.join("player-package.json").is_file() ||
            !root.join("scripts/fagrishot.py").is_file() {
            return Err("The player installation is incomplete. Extract the entire FagriShot ZIP into a fresh folder and open its launcher.".into());
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
        if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).into_owned()); }
        serde_json::from_slice(&output.stdout).map_err(|e| format!("Could not read the response from FagriShot: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn overview() -> Result<serde_json::Value, String> {
    json_command(vec!["status".into(), "--json".into()]).await
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
    let startup = tauri::Builder::default().plugin(tauri_plugin_dialog::init()).manage(Task::default()).manage(play_error.clone())
        .on_window_event(|win, ev| {
            if matches!(ev, tauri::WindowEvent::Destroyed) { let _ = stop(&win.state::<Task>()); }
        })
        .invoke_handler(tauri::generate_handler![overview, graphics, arabic_text, pick, action, cancel, startup_play_error])
        .run(tauri::generate_context!());
    if let Err(error) = startup {
        let detail = format!("{}Could not open Game setup. Check that Microsoft Edge WebView2 Runtime is installed.\n{error}",
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
