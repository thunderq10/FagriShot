# FagriShot launcher

The Windows launcher retains Tauri 2, WebView2, Vite and the upstream host CPU check. Its active Rust entry is `src-tauri/src/fagrishot.rs`. The active view is `src/fagrishot.js`. Both desktop actions and executable CLI arguments invoke the adjacent FagriShot Python tooling. The registered command set has no application download or update handler.

Build from the repository root with `python -I scripts/build_launcher.py --bootstrap`. Open `work/tools/FagriShotLauncher.exe` to select input directories, validate a dump, start the private build, manage backups or inspect diagnostics. The webview integration check runs with `node scripts/verify_launcher.mjs` and writes results under `work/launcher/`.

The upstream Rust and JavaScript pages are retained as reference for future verified player features. Cargo selects the FagriShot entry explicitly. The original source remains attributable to its upstream authors. [Foundation](../docs/foundation.md) · [Data and recovery](../docs/storage.md)
