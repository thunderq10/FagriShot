# Verification and handoff

The verification host ran Windows 11 x64 with a GeForce RTX 4090 Laptop GPU. FagriShot builds on upstream `ba87770fece1066f736e11e81e36b714ec56df0d`. The results below summarize the local playtests. Screenshots, player files and logs stay in the tester's private workspace.

| Milestone | Evidence or dependency |
| --- | --- |
| **M0 — Foundation established** | Verified host hardware and Vulkan, reviewed six candidate URLs, pinned the title-specific engine and toolchains, identified the executable profile, built native host components and tested intake. |
| **M1 — Actual game recompilation** | The six modules and descriptor matched the pinned update 131072 hashes. The local build linked a Windows x64 executable after translating 149,641 functions and 9,798,033 instructions, with zero instructions reported untranslated. |
| **M2 — Game boot and responsive menu** | The windowed game accepted Return at the Autosave prompt, played the opening movie and rendered the Casual/Classic menu and character creation. The project owner also reported a successful local playtest. |
| M3 — Gameplay | The opening chapter card, dialogue and world map have local coverage. A tactical battle, an enemy turn and chapter progression still need verification. |
| M4 — Saves and stability | A campaign save/load round trip and sustained play still need verification. File-level backup tests use synthetic data. |
| M5 — Player features tested in game | The local 0.3.0 preview rendered Arabic in the Autosave notice and Fortuna’s opening dialogue. NVIDIA DLSS Super Resolution evaluated real scene frames on the test GPU. |
| M6 — Game release candidate | Further campaign, compatibility and recovery evidence is needed. The downloadable Windows player builds the game locally from the tested input pairing. |

## Component and feature matrix

In the local 0.3.0 preview, the opening 3D scene averaged 60 FPS in three consecutive ten-second samples with 4K DLSS output, experimental reflections and Arabic text enabled. The 1% lows in those samples ranged from 54.7 to 55.9 FPS. During the chapter transition the 1% low fell to 21.8 FPS. The window presented at 1280 × 720. Campaign battles and 120 FPS performance still need measurements.

| Area | Status | Verification or dependency |
| --- | --- | --- |
| Input validation, isolated player settings and saves | Implemented and verified with fixtures | Module build IDs, full-file hashes, NPDM, optional base/update CNMT, tree hashing, wrong versions, directory separation and corruption tests. [Input profile](inputs.md) |
| CPU lifter, flags and ABI | Implemented and verified for named fixtures | AArch64 code compiled to native x64 and compared with Unicorn. Includes an unsupported-instruction case with guest PC and instruction bits. [CPU results](cpu-verification.md) |
| Runtime, filesystem, threading, audio, input and Vulkan | Implemented and verified for documented host tests | Windows loaded the runtime ABI. Synthetic GPU draws, readback, service, audio and controller fixtures ran. [Runtime results](runtime-verification.md) |
| Shader translation and disk caches | Verified on the supplied game files and synthetic fixtures | All 11,977 required NVN shaders translated. All 12,009 emitted modules passed Vulkan 1.3 validation. [Shader results](shader-verification.md) |
| GUI setup, file selection, CLI, manual local updates | Implemented and verified in part | The actual Windows WebView2 launcher displayed setup, blocked Play, saved selections under an isolated profile, displayed diagnostics and showed credits. Picker uses the native folder dialog. |
| Game executable and rendering | Windowed Vulkan presentation and keyboard confirmation verified | The guest mounted 252,686 files. With the validated shader outputs, the RTX 4090 Laptop GPU displayed the Autosave prompt and opening cinematic. Private trial receipts identify the enrolled content by its full fingerprints. |
| Backups and restoration | Implemented and verified with fixtures | Atomic replacement, restore recovery, previous-destination backup, retention and corruption checks. In-game save/load, reboot and save-format validation await M4. |
| Launcher graphics settings | Verified in the native Windows launcher | Output resolution, internal render scale, gameplay FPS target, upscaler and screen-space reflections persist across launches. A 60 or 120 FPS target depends on the scene, graphics settings and hardware. |
| NVIDIA DLSS Super Resolution | Verified in game | The RTX 4090 Laptop GPU evaluated scene color, depth and motion using the official Streamline 2.14.16 runtime. Two profiles upscaled 1280 × 720 to 2560 × 1440 and 2560 × 1440 to 3840 × 2160. The latter used a 60 FPS gameplay target and presented the 4K game image in a 1280 × 720 test window. |
| Experimental hardware ray-traced reflections | Verified on the RTX test GPU | The runtime builds a mesh from camera-visible depth and traces Vulkan rays against it before upscaling. With Arabic text and 4K DLSS output enabled, one opening-scene frame produced 5,101 sampled rays, 1,190 triangle intersections and 1,173 changed sampled colors. Geometry outside the camera depth buffer cannot appear in these reflections. |
| Arabic text preview | Verified in game | An isolated save reached the Autosave notice and the opening conversation. The game drew connected letters, punctuation and multiple lines from a private RomFS overlay. The preview covers selected opening scenes and menus. Audio uses the game’s voice language. |
| Upstream save import, DLC, multiple compatible profiles | Blocked by format and version evidence | Need matching local files and a verified round-trip. |
| Linux and Steam Deck | Unsupported in the current package | The integrated game target links for Windows. Wine/Proton compatibility and a native Linux build have separate verification requirements. |

No game benchmark or frame-rate claim is available. Record the revision, input hash, save/scene, machine and driver, resolution, settings and cache state when the first timed game scene runs.

## Session record, November 22, 2026

| Run | Result |
| --- | --- |
| Native launcher | The release PE reports product FagriShot, version 0.1.0. `node scripts/verify_launcher.mjs` executed its WebView2 flow under disposable player state. |
| Runtime | Two separate build directories generated the real DLL. Windows resolved all 16 exports. Of the selected tests, 347 cases passed and the integrated-GPU case skipped. The NVIDIA windowed and offscreen test scenes each ran for 300 frames. |
| Translator | All 15 upstream O2 programs matched Unicorn. The original CPU fixture also checked registers, vectors, NZCV and four writable regions. The upstream arithmetic flags test ran 23,757,120 checks. |
| Shaders | 46 stand-alone synthetic modules passed Vulkan 1.3 validation. For cache integration, a separate synthetic input passed through extraction, batch translation and validation. |
| Project regression suite | `python -m unittest discover -s tests -p "test_*.py" -v` passed 23 tests. See `work/project-tests.log`. |
| Initial build before game inputs arrived | The production graph configured and Ninja reported the missing `main` module. The subsequent full build using the supplied files reached M1. |
| Deliverable | `python scripts/package_tools.py` rebuilds the local host tools, archives the clean source revision's allowlist, verifies hashes and smoke-tests a fresh extraction. The adjacent verification JSON names the revision and actual package digest. |

## Pre-test verification

The Windows launcher was exercised with a disposable profile. It recovered damaged folder settings, retained unsaved edits while managing backups, and restored the selected snapshot's exact bytes. A separate cancellation check stopped both the test operation and its child process, then completed another operation in the same window. After the direct-entry, input and shader-cache changes, all 50 project regression tests passed. The log is `work/verification/direct-project-tests.log`.

All 40 project regression tests and all 146 native runtime test groups passed. The controller group exercised all nine cases using an SDL virtual controller and the Fortune’s Weave adapter. Individual native cases that require an unavailable shader corpus, another game profile, or a different GPU can skip. The shader toolchain separately validated 46 synthetic modules for Vulkan 1.3.

Double-click `work/private/game/FagriShot.exe` for a verified play attempt. Open `work/tools/FagriShotLauncher.exe` for setup, diagnostics and backups. The native direct-entry test and the release launcher’s WebView2 tests cover a successful handoff, visible file-check progress and the startup-failure diagnostic.

## Resume

Open the Windows player launcher, select both folders, click **Build game code**, then **Play**. For developers testing an uncatalogued local pairing, the source checkout supports private input enrollment. The next gameplay milestone requires a tactical action and an enemy turn. [Player guide](../README.md) · [Developer input enrollment](inputs.md)

Verification outputs and compiler products live in ignored directories. `work/cpu/` holds the CPU-oracle and game-graph logs, `work/runtime-release/Testing/Temporary/LastTest.log` has CTest cases, `tools/shaders/build/verification/` has SPIR-V evidence, and `work/launcher/ui-*/` contains the actual launcher screenshot and UI test record. The private build writes its own command log under `work/private/logs/` after inputs validate.
