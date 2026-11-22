# Building FagriShot

Use the commands in the [project README](../README.md) from the source checkout. The owner’s Windows x64 environment and the tested tool locations are recorded in [environment.md](environment.md).

| Target | Entry point | Output |
| --- | --- | --- |
| Launcher | `python -I scripts/build_launcher.py --bootstrap` | `work/tools/FagriShotLauncher.exe` |
| Runtime DLL and import library | `pwsh -NoProfile -File scripts/build_runtime.ps1 -Test -Jobs 8` | `work/runtime-release/fefw_rt.dll` and `fefw_rt.lib` |
| Shader translator | `python -I scripts/build_shaders.py --rebuild` | `tools/shaders/build/bin/Release/net10.0/spirv_harness.dll` and its dependencies |
| Private native game | `python -I scripts/fagrishot.py build-code --exefs .\exefs --jobs 8` | `work/private/game/FagriShot.exe` after a successful link |
| Per-content shader cache | `python -I scripts/fagrishot.py prepare --exefs .\exefs --romfs .\romfs` | Validated modules, resource tables and program list under the player's shader cache |
| Local tools package | `python scripts/package_tools.py` | `work/packages/FagriShot-0.1.0-Windows-x64.zip` and its verification record |

Configure the local game-build Python environment and LLVM before building private code. The full executable target uses Windows LLVM 23.1.5, the CMake/Ninja integration, the per-game configuration and the compatibility CPU variant. The project-local processor/flags oracle is separate. A clean game-graph check compiles the Windows host entry, HLE and resource objects without requiring private game libs. [Compiler and CPU evidence](cpu-verification.md)

The runtime package uses the exported `fefw_rt.dll` ABI. The current source game-build workflow links the runtime statically. Both use the same game adapter and host source. The shader tool invokes the pinned Ryubing/Ampere translator and Vulkan SDK validator. [Runtime](runtime-verification.md) · [Shaders](shader-verification.md)

The **Build game code** button and CLI invoke the same Python pipeline. It validates input module hashes, stages a prepared main, verifies the complete staged module set, builds all six libraries and links the private executable. It invalidates the finished-link record when starting a new build. Cancellation releases the process lock. The next run verifies prepared input and asks Ninja to rebuild out-of-date translation units. Interrupting an individual translation or compile can require repeating it in full. The private log records the actual commands and tool output.

Use the packaging script to make the local tools preview. The external-launcher manifest and CI release workflow of the upstream project are archived in `docs/upstream/`. No FagriShot publishing destination is configured. The original upstream build instructions remain available at the [pinned revision](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/docs/BUILDING.md).
