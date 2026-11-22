// SPDX-License-Identifier: GPL-3.0-or-later
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod cpu;

use std::io::{BufRead, BufReader, Write};
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
    exe.ancestors().skip(1).find(|p| p.join("fagrishot/__main__.py").is_file()
        && p.join("compatibility/supported.json").is_file())
        .map(|p| p.to_owned()).ok_or_else(|| "Keep the launcher with the FagriShot tools or inside its source checkout.".into())
}

fn command(args: &[String]) -> Result<Command, String> {
    let root = source()?;
    let local = root.join("tools/.venv/Scripts/python.exe");
    let mut cmd = Command::new(if local.is_file() { local } else { PathBuf::from("python") });
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

#[tauri::command]
async fn overview() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let output = command(&["status".into(), "--json".into()])?.output().map_err(|e| format!("Python 3.11+ is required: {e}"))?;
        if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).into_owned()); }
        serde_json::from_slice(&output.stdout).map_err(|e| format!("Could not read setup status: {e}"))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn action(app: tauri::AppHandle, action: String, exefs: String, romfs: String, backup_id: String) -> Result<(), String> {
    if !["configure","inspect","build-code","prepare","play","doctor","backup","backups","restore"].contains(&action.as_str()) {
        return Err("Unknown launcher action.".into());
    }
    let mut args = vec![action.clone()];
    if action == "restore" { args.push(backup_id); }
    if !matches!(action.as_str(), "doctor" | "backup" | "backups" | "restore") {
        if !exefs.is_empty() { args.extend(["--exefs".into(), exefs]); }
        if !romfs.is_empty() { args.extend(["--romfs".into(), romfs]); }
    }
    if matches!(action.as_str(),"build-code"|"play") {
        if let Some(reason) = cpu::check() { return Err(reason); }
        if let Some(note) = cpu::note() { let _ = app.emit("build-line", note); }
    }
    tauri::async_runtime::spawn_blocking(move || {
        let mut cmd = command(&args)?;
        let state = app.state::<Task>();
        let mut child = {
            let mut job = state.0.lock().unwrap();
            if job.is_some() { return Err("Another action is running.".into()); }
            let child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|e| format!("Could not run Python: {e}"))?;
            let cancellable = matches!(action.as_str(), "inspect" | "build-code" | "prepare");
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

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if !args.is_empty() {
        let result = command(&args).and_then(|mut cmd| cmd.status().map_err(|e| e.to_string()));
        match result {
            Ok(status) => std::process::exit(status.code().unwrap_or(1)),
            Err(e) => { let _ = writeln!(std::io::stderr(), "{e}"); std::process::exit(2); }
        }
    }
    tauri::Builder::default().plugin(tauri_plugin_dialog::init()).manage(Task::default())
        .on_window_event(|win, ev| {
            if matches!(ev, tauri::WindowEvent::Destroyed) { let _ = stop(&win.state::<Task>()); }
        })
        .invoke_handler(tauri::generate_handler![overview, pick, action, cancel])
        .run(tauri::generate_context!()).expect("FagriShot launcher could not start");
}
