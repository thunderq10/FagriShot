# FagriShot foundation

FagriShot retains the Fire Emblem-specific nxrecomp fork at `ba87770fece1066f736e11e81e36b714ec56df0d`. This checkout preserves its Git history. The project owner maintains the FagriShot changes. Individual upstream contributors keep their authorship.

## Candidate review on 22 November 2026

| Repository and branch | Revision | Source evidence and decision |
| --- | --- | --- |
| metrist38/fefw-recomp, master | `ba87770fece1066f736e11e81e36b714ec56df0d` | Selected. Six module definitions, per-game lift configuration, host adapters, dump hashes, build targets, a Vulkan backend and launcher are in the same revision. [Source tree](https://github.com/metrist38/fefw-recomp/tree/ba87770fece1066f736e11e81e36b714ec56df0d) |
| may22es/fefw-recomp, master | `0a7954b3a578031fa49be118f26f57d8688cef23` | Earlier version of the same game project. Its release manifest has the same hashes. GitHub compares the selected revision as 551 commits ahead and zero behind. The later tree also contains CPU flag regression tests and a path-handling test. [Comparison](https://github.com/metrist38/fefw-recomp/compare/0a7954b3a578031fa49be118f26f57d8688cef23...ba87770fece1066f736e11e81e36b714ec56df0d) |
| drakolordx7/nxrecomp, main | `0a9fe4653648cab485864524a30997b75589df9d` | Shared lifter, runtime and tools under `base/`. The checked revision lists game adapters for Metroid Prime Remastered and Hyrule Warriors: Age of Calamity. Neither implements this title. [Game tree](https://github.com/drakolordx7/nxrecomp/tree/0a9fe4653648cab485864524a30997b75589df9d/games) |
| drakolordx7/switch2-recomp, main | `5339bf85b05e84c34a7f18d8a43db1de1f1692b2` | Fork of ArtBreguez/pokopia-recomp. Its `pkp` game configuration, host adapters and launcher target Pokémon Pokopia 2.0.1a. Both the project and selected fork credit shared nxrecomp components. [Source tree](https://github.com/drakolordx7/switch2-recomp/tree/5339bf85b05e84c34a7f18d8a43db1de1f1692b2) |
| MagicBeanDev/Longshot, main | `a2dc5385901812d183c4b7f0f9d48b295f17a510` | Architectural reference. Its separate lifter and host are specific to its supported game. The review covers both the website and Forgejo source. [Longshot review](longshot.md) |

## Retained architecture

| Layer | Evidence in the selected revision |
| --- | --- |
| Executable input | `game-build/release/dump_hashes.json` records program ID `04007EF01F8AE000`, update `131072`, the main build ID, six NSO digests, the process metadata digest and a separate plain-main digest. The upstream launcher checks the RomFS directory exists. Its presence alone cannot identify the content revision. |
| CPU | `recomp/nxrecomp/` reads NSOs, discovers functions and emits C++ shards with module descriptors. The compiler links these into the native target. `recomp/games/fefw/config.py` pins module placement, build ID and seven native substitutions with instruction-word guards. `main.sites` records hook locations, and `hot.txt` and `inline.txt` contain upstream profiling addresses. These files are game-specific compatibility metadata. |
| Loader and services | `game-build/fefw/src/fefw_main.cpp` and `runtime/games/fefw/` provide entry and game hooks. `runtime/src/services/` contains filesystem, controller, time, audio and profile implementations. `runtime/src/ipc/stub.cpp` returns a recognizable error by default. Its diagnostic `stub_result=0` setting fabricates an empty success and must stay outside compatibility validation. |
| Graphics | `runtime/src/gfx/nvn/` implements NVN calls, texture and descriptor handling, submission and Vulkan presentation. The separate GPU stub is linked by unit tests. `tools/shaders/ampere/` decodes Ampere machine instructions, and the C# harness uses Ryujinx shader IR and SPIR-V generation. Maxwell test samples alone cannot validate this game's scenes. |
| Windows binaries | `game-build/fefw/CMakeLists.txt` separates the reusable runtime-DLL target from the game target that consumes six lifted libraries. The DLL filename, C API, import library, module descriptors, INI section IDs and save identifiers are compatibility interfaces. Product titles and output filenames are separate. |
| Frontend | The inherited launcher uses a Vite frontend with Tauri/Rust commands for probing, setup, play, settings and updates. Its updater's compile-time fallback in `launcher/src-tauri/src/update.rs` points to `aidankinzett/fefw-recomp`. FagriShot must reach only its configured local/manual update flow until it has its own release service. |

The CPU checks use original instruction fixtures and the upstream reference programs. Upstream Windows CTest coverage excludes selected GPU tests and lift tests that need a dump. Renderer parity, frame timing, game audio and save safety all require additional execution evidence. [Local CPU results](cpu-verification.md)

## Build and dependency provenance

The upstream source pipeline prepares private NSOs, lifts all six modules, compiles them, builds the runtime and prepares SPIR-V from the supplied content. The packaged path instead uses bundled Zig to link the player-generated objects with the runtime import library. Its cache stamp covers the lifter, game sites, CPU variant, flags, SDK files and runtime import library. Only the packaged path implements the cancellation and completion markers described in `game-build/release/fefw_build.py`. A native Linux launcher for this upstream uses the Windows game through Wine or Proton. [Pinned build guide](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/docs/BUILDING.md)

The reviewed build scripts include `build_fefw.ps1`, `fefw_build.py` and `make-release.ps1`. The source wrapper can recreate its build directory when caches disagree. The release packager recreates its staging directory and downloads tools. Its Python embeddable archive fetch supplies no SHA-256 argument and some inherited package paths use upstream product names. FagriShot's local package must use its own allowlist and integrity checks.

Runtime CMake pins SDL 3.4.18, Vulkan-Headers and volk at Vulkan SDK 1.4.363.0, VMA 3.4.0, Dear ImGui 1.92.9b and zstd 1.5.7 by archive hash. The source shader build specifies Ryubing commit `a82350bb774f70fcbd41c9987bf67a3775409963`; the .NET harness stays a separate dependency. Texture transcoding uses ASTC Encoder 5.7.1 and bc7enc_rdo `b9438627eef73a1157e84201b6fa6eb2ffd6d9f0`. [Graphics dependencies](../runtime/cmake/gfx_deps.cmake) · [Texture sources](../tools/textures/scripts/fetch_third_party.ps1)

## Licenses and attribution

The tree is distributed under GPLv3 with individual files marked GPL-3.0-or-later and separately licensed third-party components. Preserve the top-level and component licenses, headers, notices, art credits, vendor terms and dependency licenses with the corresponding components. The FagriShot additions in this checkout use GPL-3.0-or-later. The upstream shader samples identify Sascha Willems' Vulkan examples as their source. The checkout also contains annotated game hook addresses, upstream sample ELF fixtures, a prompt asset package, upstream icons and the attributed RenHDRum DLL. Game executable modules, locally generated game code, shader output and player files belong in private work areas. [Project license](../LICENSE) · [CPU sources and revisions](../recomp/THIRD_PARTY.md) · [Runtime notices](../runtime/THIRD_PARTY_NOTICES.md) · [Shader licenses](../tools/shaders/licenses)
