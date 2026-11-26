# Building FagriShot

This page is for developers building FagriShot from source. Players can use the [Windows launcher](../README.md).

## Developer prerequisites and bootstrap

- Use Windows x64, a processor with SSE4.2, POPCNT and CMPXCHG16B, and a Vulkan 1.3-capable graphics driver. The verified configuration used Windows 11 and an NVIDIA GeForce RTX 4090 Laptop GPU. [Tested environment](environment.md)
- Install Git, PowerShell 7, **64-bit CPython 3.13** available as `python`, and Node.js 22.12 or later with npm.
- Install Visual Studio or Visual Studio Build Tools with the **Desktop development with C++** workload, the x64 MSVC tools, a Windows SDK, and the C++ CMake tools for Windows. The build uses CMake 3.25 or later and Ninja.
- Install the Vulkan SDK and make sure its `spirv-val` and `spirv-dis` commands are on `PATH`. The setup window uses Microsoft Edge WebView2 Runtime.

Open PowerShell 7. Clone this repository and run the remaining commands from its root. The first build downloads pinned dependencies, including LLVM, Rust and the .NET shader tools. [Dependency provenance](foundation.md)

```powershell
git clone https://github.com/thunderq10/FagriShot.git
cd FagriShot
python -I scripts/setup_python.py
.\tools\.venv\Scripts\python.exe -I scripts/setup_llvm.py
python -I scripts/build_shaders.py --rebuild
python -I scripts/build_launcher.py --bootstrap
```

## Build targets

| Target | Entry point | Output |
| --- | --- | --- |
| Launcher | `python -I scripts/build_launcher.py --bootstrap` | `work/tools/FagriShotLauncher.exe` |
| Runtime DLL and import library | `pwsh -NoProfile -File scripts/build_runtime.ps1 -Test -Jobs 8` | `work/runtime-release/fefw_rt.dll` and `fefw_rt.lib` |
| Shader translator | `python -I scripts/build_shaders.py --rebuild` | `tools/shaders/build/bin/Release/net10.0/spirv_harness.dll` and its dependencies |
| Private native game | `python -I scripts/fagrishot.py build-code --exefs .\exefs --jobs 8` | `work/private/game/FagriShot.exe` after a successful link |
| Per-content shader cache | `python -I scripts/fagrishot.py prepare --exefs .\exefs --romfs .\romfs` | Validated modules, resource tables and program list under the player's shader cache |
| Windows player ZIP | `python scripts/package_player.py` | `work/packages/FagriShot-Windows-x64.zip` and its verification record |
| Local tools package | `python scripts/package_tools.py` | `work/packages/FagriShot-<version>-Windows-x64.zip` and its verification record |

Configure the local game-build Python environment and LLVM before building private code. The full executable target uses Windows LLVM 23.1.5, the CMake/Ninja integration, the per-game configuration and the compatibility CPU variant. The project-local processor/flags oracle is separate. A clean game-graph check compiles the Windows host entry, HLE and resource objects without requiring private game libs. [Compiler and CPU evidence](cpu-verification.md)

The runtime package uses the exported `fefw_rt.dll` ABI. The current source game-build workflow links the runtime statically. Both use the same game adapter and host source. The shader tool invokes the pinned Ryubing/Ampere translator and Vulkan SDK validator. [Runtime](runtime-verification.md) · [Shaders](shader-verification.md)

The launcher and the `build-and-prepare` command validate the selected ExeFS and RomFS, build the six modules and prepare shaders. A source checkout uses the CMake and Ninja workflow. The distributed player uses its bundled Python and Zig compiler, links against the packaged runtime, and stores its build in `%LOCALAPPDATA%\FagriShot\build` for the Windows account. An interrupted player build can reuse verified outputs on its next run. The build log records the compiler commands and progress.

To assemble the Windows player, build the release launcher, runtime and shader toolchain first. Point `--runtime` at the runtime build directory with the DLL and fetched dependency notices, and `--launcher-cache` at the Cargo and Rust cache created by the launcher build. The packager verifies a fresh extraction and writes its report alongside the ZIP. The separate `package_tools.py` script produces the developer tools preview. The external-launcher manifest and CI release workflow of the upstream project are archived in `docs/upstream/`. Windows player builds are distributed through [GitHub Releases](https://github.com/thunderq10/FagriShot/releases). The original upstream build instructions remain available at the [pinned revision](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/docs/BUILDING.md).
