# Verification and handoff

The verification host is Windows 11 x64 with a GeForce RTX 4090 Laptop GPU. The initial workspace contained no project or game files. The project owner identified that folder as the input location. The checkout on branch `fagrishot` has local commits over upstream `ba87770fece1066f736e11e81e36b714ec56df0d`. Use `git rev-parse HEAD` and `git status --short --branch` to identify the current revision and local changes. There is no FagriShot remote.

| Milestone | Evidence or dependency |
| --- | --- |
| **M0 — Foundation established** | Verified host hardware and Vulkan, reviewed six candidate URLs, pinned the title-specific engine and toolchains, identified the executable profile, built native host components and tested intake. |
| M1 — Actual game recompilation | Blocked on the six ExeFS modules and main.npdm from update 131072. A clean production configure generated the six library targets and compiled the genuine host entry/HLE/resource objects. Ninja stops on the missing original main. |
| M2 — Title/menu | Blocked on M1 and a validated matching merged RomFS. No title screen or game frame recorded. |
| M3 — Gameplay | Blocked on M2. No chapter, route, tactical action, enemy turn, cutscene or transition has local coverage. |
| M4 — Saves and stability | Blocked on a playable title and an ordinary game-created save. File-level backup tests use synthetic data. |
| M5 — Player features tested in game | Blocked on playable baseline and representative saves. |
| M6 — Local game release candidate | Blocked on gameplay, compatibility and recovery evidence. The current archive is a development tools package. |

## Component and feature matrix

| Area | Status | Verification or dependency |
| --- | --- | --- |
| Input validation, isolated player settings and saves | Implemented and verified with fixtures | Module build IDs, full-file hashes, NPDM, optional base/update CNMT, tree hashing, wrong versions, directory separation and corruption tests. [Input profile](inputs.md) |
| CPU lifter, flags and ABI | Implemented and verified for named fixtures | AArch64 code compiled to native x64 and compared with Unicorn. Includes an unsupported-instruction case with guest PC and instruction bits. [CPU results](cpu-verification.md) |
| Runtime, filesystem, threading, audio, input and Vulkan | Implemented and verified for documented host tests | Windows loaded the runtime ABI. Synthetic GPU draws, readback, service, audio and controller fixtures ran. [Runtime results](runtime-verification.md) |
| Shader translation and disk caches | Implemented and verified for synthetic shaders | Real NVIDIA instruction decoding, independent SPIR-V validation, cache invalidation and output-corruption tests. [Shader results](shader-verification.md) |
| GUI setup, file selection, CLI, manual local updates | Implemented and verified in part | The actual Windows WebView2 launcher displayed setup, blocked Play, saved selections under an isolated profile, displayed diagnostics and showed credits. Picker uses the native folder dialog. |
| Game executable and rendering | Blocked by game inputs | No approved content pairing or privately linked game binary exists on this host. |
| Backups and restoration | Implemented and verified with fixtures | Atomic replacement, restore recovery, previous-destination backup, retention and corruption checks. In-game save/load, reboot and save-format validation await M4. |
| Display settings, device changes, volume, subtitles, languages and accessibility | Implemented in part upstream, unverified in game | Launcher exposes setup and diagnostics. The integrated engine retains its F1 overlay, controller and mouse paths. Actual game interaction and DPI coordination require gameplay. |
| MOD support, optional frame rates and advanced graphics | Blocked by baseline verification | Upstream implementations remain available for investigation. Baseline uses original timing and disables mod DLLs and overrides. Ultrawide, HDR, upscaling or frame generation need scene and timing tests. |
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
| Current failed game build | `pwsh -NoProfile -File scripts/build_game.ps1 -Stage Lift -Jobs 2` configured the actual production graph. Ninja reported the missing `work/private/prepared-exefs/main` needed for `generated/main/c_000.cpp`. See `work/verification/game-lift.log`. |
| Deliverable | `python scripts/package_tools.py` rebuilds the local host tools, archives the clean source revision's allowlist, verifies hashes and smoke-tests a fresh extraction. The adjacent verification JSON names the revision and actual package digest. |

## Resume

Open the local launcher or run the commands in the README. To reproduce the current blocker, run `python -I scripts/fagrishot.py build-code --exefs .\exefs`. It returns exit code 2 at input validation. The smallest remaining M1 dependency is the extracted ExeFS. Once available, build from that folder. To establish M2, inspect a matching merged RomFS, preserve independent evidence of the update pairing, and use an isolated save directory for the first title/menu test.

Verification outputs and compiler products live in ignored directories. `work/cpu/` holds the CPU-oracle and game-graph logs, `work/runtime-release/Testing/Temporary/LastTest.log` has CTest cases, `tools/shaders/build/verification/` has SPIR-V evidence, and `work/launcher/ui-*/` contains the actual launcher screenshot and UI test record. The private build writes its own command log under `work/private/logs/` after inputs validate.
