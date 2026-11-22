# Native Windows runtime

Build the runtime from its pinned CMake dependencies using PowerShell 7 x64 with LLVM clang-cl on PATH and the Visual Studio x64 C++ build tools installed. The command uses the CMake and Ninja on PATH, or the copies bundled with Visual Studio. First setup downloads the dependency sources and assembler. Subsequent builds reuse the checkout-local cache and Ninja dependency graph.

```powershell
pwsh -NoProfile -File .\scripts\build_runtime.ps1 -Test -Jobs 8
```

To test from a new build tree, pass `-BuildName clean` when `work/runtime-release-clean` is absent. A different name creates a different tree. The shared dependency cache survives. The build lock prevents concurrent wrapper invocations from writing that cache together.

`work/runtime-release/fefw_rt.dll` contains the host, graphics code and title adapter. Its import library `fefw_rt.lib` is for the later private game link. Keep the DLL name and the dynamic symbols defined in `game-build/fefw/src/fefw_rt.def` when moving files. The debugger symbols and DejaVu fallback font are generated/copied alongside the DLL.

The wrapper configures the existing `FEFW_RT_DLL` target with the C++ ASTC→BC7 transcoder and assembly-accelerated dav1d. The SDL3, Vulkan-Headers, volk, VMA, Dear ImGui and zstd versions and SHA-256 digests are in `runtime/cmake/gfx_deps.cmake`. The two texture source pins are in `tools/textures/scripts/fetch_third_party.ps1`. The assembler version and SHA-512 digest are in `scripts/build_runtime_tools.cmake`. Archives live under `work/runtime-dependencies` and the two texture source trees under `tools/textures/third_party`. All are ignored by Git. The compiler, dependency change tracking and cancellation operate at the CMake/Ninja step boundaries. Repeating the command after interruption reconfigures and builds missing or out-of-date targets.

`-Test` asks the Windows loader to resolve the DLL exports, then runs selected CTest groups for runtime module registration, SVC memory, light events, filesystem IPC, audio buffer IPC, video frame conversion, controller mapping, NVN clear/present/readback and GPU ASTC conversion. The tests create disposable fixtures. CTest writes the case output and outcomes to `work/runtime-release/Testing/Temporary/LastTest.log`. The audio, video and save fixtures exercise individual runtime contracts. Game data, cutscenes, and player saves require separate execution evidence.

## November 22, 2026 results

The initial assembly-enabled verification against `ba87770fece1066f736e11e81e36b714ec56df0d` plus the working changes produced a 13,243,904-byte DLL with SHA-256 `9c72ff12795b39e058b0a72c279dfae7c2c2961c08860326a7c7d7b154b912b5`. Windows resolved all 16 declared exports. Independent inspection with `llvm-readobj --coff-exports` and `llvm-nm --defined-only --extern-only` found the same symbols in the DLL and its 4,002-byte import library, including the three ABI data symbols. The configuration compiled 25 dav1d assembly objects. The current NASM command uses `-MD` to track assembly includes. On versions that support it, `--reproducible` zeroes the COFF timestamp. The optional libvpx library was absent, so this build omits its host VP8/VP9 decoding path.

Of 146 registered native CTest groups, 28 ran. Across those groups, 347 cases passed, zero failed and one skipped. The skip was the integrated-GPU scene check because Vulkan exposed only the NVIDIA adapter. Runtime fixtures exercised relocated ELF segments, import registration and refusal of incompatible modules, exception dispatch, clocks, synchronization, memory, temporary save creation and readback, controller mapping, audio buffering/mixing and video frame conversion. Upstream's synthetic boot fixture includes stub services. Its results do not verify a game boot.

The RTX 4090 Laptop GPU rendered the runtime's synthetic scene for 300 frames offscreen and 300 frames through a Windows window. Both reported zero validation errors or warnings. The screenshot comparisons matched the stored reference. The NVN clear fixture read back the requested color from all 3,072 pixels of its 64 × 48 target. GPU ASTC decoding matched the CPU reference for the tested images, and BC7 encode, queue and cache fixtures passed. These results have no game fingerprint or gameplay frame-rate measurement.

The local build and fixture transcripts are `work/runtime-verification.log` and `work/runtime-extended.log`. The corresponding test sources are under `runtime/tests`. Pinned Vulkan-Headers and volk archives with the same upstream basename use distinct verified cache filenames.

The final local build identity and binary digests are written to `work/runtime-final-summary.json` during session verification. The runtime About screen embeds the source commit at configure time.
