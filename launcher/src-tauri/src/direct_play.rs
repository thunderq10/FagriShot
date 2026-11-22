// SPDX-License-Identifier: GPL-3.0-or-later
use std::io::{self, BufRead, BufReader, Read};
use std::process::Stdio;

#[cfg(windows)]
mod progress {
    use windows::core::w;
    use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED};
    use windows::Win32::UI::Shell::{CLSID_ProgressDialog, IProgressDialog, PROGDLG_MARQUEEPROGRESS, PROGDLG_NOCANCEL};

    pub struct Dialog(Option<IProgressDialog>);

    impl Dialog {
        pub fn show() -> Option<Self> {
            // The Shell updates its modeless progress dialog on its own thread while Python hashes the files.
            unsafe {
                CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok().ok()?;
                let dialog: Option<IProgressDialog> = CoCreateInstance(&CLSID_ProgressDialog, None, CLSCTX_INPROC_SERVER).ok();
                if let Some(d) = &dialog {
                    let _ = d.SetTitle(w!("FagriShot"));
                    let _ = d.SetLine(1, w!("Checking game files before Play."), false, None);
                    let _ = d.SetLine(2, w!("Checking every file can take several minutes."), false, None);
                    let _ = d.StartProgressDialog(None, None, PROGDLG_MARQUEEPROGRESS | PROGDLG_NOCANCEL, None);
                }
                Some(Self(dialog))
            }
        }
    }

    impl Drop for Dialog {
        fn drop(&mut self) {
            unsafe {
                if let Some(dialog) = self.0.take() {
                    let _ = dialog.StopProgressDialog();
                }
                CoUninitialize();
            }
        }
    }
}

// The game can write diagnostics for its entire session. Keep just the tail for a failed Play.
fn last_diagnostic(mut stderr: impl Read) -> String {
    let mut tail = Vec::new();
    let mut block = [0; 4096];
    while let Ok(n) = stderr.read(&mut block) {
        if n == 0 { break; }
        tail.drain(..(tail.len() + n).saturating_sub(block.len()));
        tail.extend_from_slice(&block[..n]);
    }
    String::from_utf8_lossy(&tail).trim().to_owned()
}

pub fn run() -> Result<(), String> {
    if let Some(reason) = super::cpu::check() { return Err(reason); }
    #[cfg(windows)]
    let mut progress = progress::Dialog::show();
    let mut child = super::command(&["play".into()])?
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()
        .map_err(|e| format!("Could not run Play. Install Python 3.11 or later and try again. {e}"))?;
    let stderr = child.stderr.take().unwrap();
    let diagnostic = std::thread::spawn(move || last_diagnostic(stderr));
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    let mut line = Vec::new();
    while stdout.read_until(b'\n', &mut line).unwrap_or(0) > 0 {
        if line.trim_ascii_end() == b"Starting FagriShot." {
            #[cfg(windows)]
            drop(progress.take());
            // Subsequent output can come from the native game itself.
            let _ = io::copy(&mut stdout, &mut io::sink());
            break;
        }
        line.clear();
    }
    drop(stdout);
    let status = child.wait().map_err(|e| e.to_string())?;
    let message = diagnostic.join().unwrap_or_default();
    if status.success() { return Ok(()); }
    Err(if message.is_empty() { format!("Play exited with {status}. Check the FagriShot log in Local files.") } else { message })
}
