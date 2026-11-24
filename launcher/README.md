# FagriShot launcher

The local preview's **Graphics settings → Experimental ray-traced reflections** is off by default. On compatible Vulkan GPUs it traces the scene surfaces visible to the camera. The independent **Screen-space reflections** option controls the original game effect.

Open `FagriShotLauncher.exe` in the extracted Windows player folder, choose the ExeFS and merged RomFS from your own update 131072 extraction, and select **Build game code**. The launcher verifies the files, compiles the native executable and prepares shaders. It enables **Play** when the backend reports a finished build for the selected saved locations. **Local files** contains diagnostics, backups and restore.

In the local 0.3.0 preview, open **Graphics settings** beside **Play**. Choose an output resolution, 3D render scale, gameplay frame rate, upscaler and screen-space reflection quality. **Save graphics settings** applies your choices on the next launch. For native 4K, select **3840 × 2160 (4K)** and **100% (native)**. At 50%, the game renders 3D at 1920 × 1080 and upscales it to 4K. The 60 and 120 FPS settings are gameplay targets. You can check achieved frame rate with **F2**, and actual render and output sizes under **F1 → Graphics → Live render state**.

TAAU and bilinear scaling are built in. NVIDIA DLSS requires an RTX GPU, a compatible driver and the four Streamline files `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll` and `nvngx_dlss.dll`. The launcher checks for them under `upscalers/streamline` in the player folder and in the game's other supported locations. The game checks GPU and driver support at startup. The optional **Arabic text preview** reports its current coverage beside the switch in Game setup.

The Tauri entry is `src-tauri/src/fagrishot.rs` and its WebView2 interface is `src/fagrishot.js`. Player installations use the bundled interpreter in `tools/python/`. The source build uses the checkout’s Python environment. Build the launcher from the repository root with `python -I scripts/build_launcher.py --bootstrap`. The output is `work/tools/FagriShotLauncher.exe`. Run `node scripts/verify_launcher.mjs --direct` to check the native pickers, build progress and cancellation, Play, diagnostics, saves and direct-entry handoff using isolated fixtures.

The upstream Rust and JavaScript pages are retained as reference for future verified player features. Cargo selects the FagriShot entry explicitly. The original source remains attributable to its upstream authors. [Foundation](../docs/foundation.md) · [Data and recovery](../docs/storage.md)
