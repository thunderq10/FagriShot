# FagriShot Windows packages

The local 0.3.0 preview writes **FagriShot-0.3.0-preview-Windows-x64.zip**. Its Graphics settings and Arabic text preview controls appear in Game setup. The package includes the translation catalog and DejaVu Sans. The player's verified inputs supply the game text and fonts used to generate the private overlay at first launch with Arabic enabled. Settings and backups are accessible under **Local files**.

For the Windows player, download **FagriShot-Windows-x64.zip**, extract it into a writable folder and open **FagriShotLauncher.exe**. Choose your supported ExeFS and RomFS folders, click **Build game code**, then **Play**. The ZIP includes the compiler, Python, shader translator and validator. Windows 11 x64 supplies the desktop environment. The launcher uses Microsoft Edge WebView2 and the game needs a Vulkan 1.3-capable graphics driver. [Player guide](../README.md) · [دليل التشغيل](../README.ar.md)

## Developer tooling preview

Extract the entire archive into its own directory. Use Windows 11 x64, Python 3.13 x64 on PATH as `python`, and the Microsoft Edge WebView2 Runtime for the graphical launcher. Run `FagriShotLauncher.exe` for the desktop interface. The same executable forwards command-line arguments to the adjacent Python tools.

```powershell
.\FagriShotLauncher.exe status --json
python -I .\scripts\fagrishot.py doctor
python -I .\scripts\fagrishot.py inspect --exefs D:\dump\exefs --romfs D:\dump\romfs
```

The preview includes input inventory and compatibility checks, save backup tools, the FagriShot launcher, and the host runtime DLL, import library and fallback font under `runtime-bin\`. The game-specific translator, compiler, shader preparation toolchain and private game build run from the corresponding local source checkout. See `SOURCE.txt` for its commit and `docs/inputs.md` for the exact executable and content gates. There is no verified boot or gameplay claim for this package.

Keep `FagriShotLauncher.exe`, `scripts\`, `fagrishot\` and `compatibility\` together. Settings, saves, backups, caches and logs use `%LOCALAPPDATA%\FagriShot\`. To update manually, extract a complete replacement into a new folder and retain the previous folder until the replacement passes diagnostics. Preserve the separate user-data directory. Online updating is unavailable.

From a built source checkout, package the tools locally with the following command. It requires the project-local Rust and Cargo cache from the launcher build and the dependency sources from the runtime build. Missing license files in a few Cargo crates are supplemented from pinned upstream sources. Only license texts are fetched.

```powershell
python scripts/package_tools.py
```

The builder runs the normal launcher and runtime builds against the committed source before final packaging. It checks archive bytes against the SHA-256 manifest, extracts into a fresh directory outside the checkout, invokes the packaged launcher and CLI independently against isolated state, and resolves each declared runtime DLL export on Windows. It also checks the packaged runtime path, folder selection, rejection of missing game inputs and a backup/restore round trip with an original synthetic file. It writes the zip and verification record under `work/packages/`. For development, `--draft` uses the existing binaries, permits an explicitly listed set of work-in-progress source files and marks the manifest accordingly. `manifest.json` identifies the exact payload bytes for comparison. It is unsigned.

The maintainer's local source checkout at the recorded revision is the accompanying source for this private package. Keep it with the archive. The project uses GPL-3.0-or-later and preserves third-party license terms, exact Cargo and npm dependency versions, Rust library notices, and the SDL, Vulkan, font, video, texture and other runtime notices in `licenses/`. The included storage guide covers backup retention and restoration. The status report distinguishes host fixture results from game milestones.
