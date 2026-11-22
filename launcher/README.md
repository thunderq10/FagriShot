# FagriShot launcher

Open `FagriShotLauncher.exe` in the extracted Windows player folder, choose the ExeFS and merged RomFS from your own update 131072 extraction, and select **Build game code**. The launcher verifies the files, compiles the native executable and prepares shaders. It enables **Play** when the backend reports a finished build for the selected saved locations. **Local files** contains diagnostics, backups and restore.

The Tauri entry is `src-tauri/src/fagrishot.rs` and its WebView2 interface is `src/fagrishot.js`. Player installations use the bundled interpreter in `tools/python/`. The source build uses the checkout’s Python environment. Build the launcher from the repository root with `python -I scripts/build_launcher.py --bootstrap`. The output is `work/tools/FagriShotLauncher.exe`. Run `node scripts/verify_launcher.mjs --direct` to check the native pickers, build progress and cancellation, Play, diagnostics, saves and direct-entry handoff using isolated fixtures.

The upstream Rust and JavaScript pages are retained as reference for future verified player features. Cargo selects the FagriShot entry explicitly. The original source remains attributable to its upstream authors. [Foundation](../docs/foundation.md) · [Data and recovery](../docs/storage.md)
