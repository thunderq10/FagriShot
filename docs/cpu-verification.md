# CPU translation and compiler verification

## Reproduce on Windows x64

Run these commands in PowerShell at the project root. Use a 64-bit CPython 3.13 executable in place of `python3.13` if your installation uses a different name.

```powershell
python3.13 -I scripts/setup_python.py
tools/.venv/Scripts/python.exe -I scripts/setup_llvm.py
tools/.venv/Scripts/python.exe -I tests/cpu/verify_nso.py
tools/.venv/Scripts/python.exe -I tests/cpu/verify_entry.py
python3.13 -I scripts/verify_cpu.py
pwsh -NoProfile -File scripts/verify_cpu_graph.ps1
```

The first command installs the six pinned game-build wheels in `tools/.venv`, checks their package dependencies and imports their modules. Their hashes are in `scripts/requirements-win64-py313.txt`. The compiler setup checks LLVM's Windows archive against the SHA-256 used by upstream CI and extracts the build tools into `work/toolchain/llvm-23.1.5/bin`. The CPU verification creates a separate two-package reference environment under `work/cpu/venv`. It creates a fresh run directory on each invocation. Each run stores its commands, compiler output, disassemblies, synthetic ELFs, generated C++, Windows test executables and machine-state comparison data there. [LLVM release](https://github.com/llvm/llvm-project/releases/tag/llvmorg-23.1.5)

## Observed on 22 November 2026

The Windows CPU run used LLVM 23.1.5 (`392dfff97d29b59d77e5cac6d4b6ef2585fe7117`), MSVC v143 headers 14.44.35207, CPython 3.13.17 x64, Unicorn 2.1.4 and Capstone 5.0.9. The verification program also passed with the existing LLVM 20.1.0 and the same v143 headers. The system installation of LLVM stayed in place. The project-local compiler is the supported build default. Compilation produced one upstream warning about a `noreturn` annotation in the test exception runner. These fixtures exercise normal returns.

| Check | Result |
| --- | --- |
| ARM64 static recompilation | Assembled the original fixture and ran the actual nxrecomp static ELF pipeline. It discovered two functions, lifted 30 instruction words with zero untranslated, emitted a module table and linked a Windows AMD64 PE that executed successfully. |
| NSO preparation | Invoked the actual unpacker CLI on original plain, LZ4 and ZBIC fixtures with three segments. All prepared bytes matched the expected plain module and the nxrecomp NSO reader checked its segment bytes, addresses and digests. An intentionally corrupted second-segment digest failed with the specified segment and preserved both source and previous destination. Evidence in `work/cpu/nso-m4s7e0j2/verification.log`. |
| CPU state | The program exercises integer overflow and carry, direct and indirect calls, conditional control flow, data loads and stores, TLS access, vector lanes and scalar FP. The independently executed AArch64 image under Unicorn and the native binary matched in x0–x30, SP, v0–v31, NZCV and the SHA-256 of all 1,179,680 bytes across writable image data, stack and both TLS regions. |
| Unsupported operation | The second ELF contains ARMv8.2-A SHA512H, an optional extension chosen to reach the pinned translator's unsupported path. It reported one untranslated word. Its native runner exited 11 and printed the expected fixture PC `0x7100010120`, instruction `ce628020` and `t2:sha512h`. The diagnostic makes no assumption about whether this game uses the extension. |
| Upstream flags regression | Built and ran `recomp/tests/nxflags_test.cpp`. It reported 23,757,120 condition checks and zero failures. This compares two flag representations in the same codebase. The separate Unicorn run provides the independent CPU comparison. |

LLVM 23 run evidence is in `work/cpu/run-utxwxftw/verification.log`. The earlier LLVM 20 run is in `work/cpu/run-qv50_dya/verification.log`. The test executables have the name `cpu_fixture.exe`. M1 requires all six modules from the identified Fortune's Weave image.

## Production CMake graphs

The six-module lift project configured with the compatibility CPU variant, LLVM 23.1.5, VS 2026 Community v14.52.37025 headers, Windows SDK 10.0.26100.0, CMake 4.3.1-msvc1 and Ninja 1.13.2. The graph includes an actual lift and static library target for each module. Invoked against its isolated empty input location, Ninja exited 1 at the dependency on `exefs/main`, before generating game code. The game link graph named all six expected libraries on `FagriShot.exe`. Its genuine C++ entry object, native zstd adapter object and Windows resource compiled successfully. The generated `build.ninja` files, query results and missing-input diagnostic from the first run are under `work/cpu/graph-19c6b5f33a934303aea7725da676625b/`.

A second clean configure verified the corrected Vulkan archive filenames and NASM depfiles. Evidence is in `work/cpu/graph-af91130db426438787d05d07c0d24f4e/`, including separate configure logs, the missing-input diagnostic, the object build log and the later recompilation of the direct-entry guard.

## Direct game entry and saves

Starting the game executable without arguments opens the adjacent `FagriShotLauncher.exe`, or the marked checkout's `work/tools/FagriShotLauncher.exe` when the game is in `work/private/game`. The launcher's managed child supplies `--ini`. Explicit diagnostics require a readable INI with an absolute `[fs] save_dir`. The process pins that path before the runtime registers its filesystem services.

The native entry fixture uses the production path-selection code, process launcher and INI parser. It checks both launcher locations with real child processes, rejects an unrelated checkout and ignores the working directory and PATH. Missing, empty and relative save roots fail even with a competing ambient save setting. A valid absolute save path survives a simulated subsequent configuration reload. The fixture writes only to its temporary workspace.

## Upstream CPU reference programs

All 15 upstream end-to-end programs passed with their guest images compiled at `-O2`. They exercise integer and floating-point operations, jump tables, SIMD, crypto/CRC, atomics, exclusive access, TLS, indirect calls, tail calls and exception handlers. The upstream runner compiles its own AArch64 ELFs, lifts them, builds native Windows runners and checks registers and writable memory against Unicorn. The run is recorded in `work/cpu/upstream-812aa26e8de8483d8dface9e0a4cb257/verification.log`.

Enabling the same `flags,vstore` optimization settings as the Fortune's Weave configuration also passed 30/30 comparisons across the same 15 programs compiled at both `-O2` and `-O0`. In this mode the upstream comparator excludes final NZCV because it is call-clobbered under AAPCS64. The unoptimized lifter checks above include final NZCV. The private game modules and title hooks are outside this suite. See `work/cpu/optimized-e38c6bd4fb9b47de8ffc7e6c91c82fc3/verification.log`.

Reproduce with the compiler and Python environment set up above. Use a fresh `--build` directory for each optimization configuration.

```powershell
$finder = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $finder -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$env:NX_LLVM = (Resolve-Path 'work/toolchain/llvm-23.1.5/bin').Path
$env:LLVM_BIN = $env:NX_LLVM
$env:NX_VCVARS = Join-Path $vs 'VC/Auxiliary/Build/vcvars64.bat'
$cm = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake'
$env:NX_TOOLS = "$cm/CMake/bin;$cm/Ninja"
Remove-Item Env:NXRECOMP_OPT -ErrorAction SilentlyContinue
$out = Join-Path (Get-Location) 'work/cpu/reference-o2'
tools/.venv/Scripts/python.exe -I recomp/oracle/e2e/run_e2e.py --opts O2 --jobs 4 --build $out
$env:NXRECOMP_OPT = 'flags,vstore'
$out = Join-Path (Get-Location) 'work/cpu/title-options'
tools/.venv/Scripts/python.exe -I recomp/oracle/e2e/run_e2e.py --opts O2,O0 --jobs 8 --build $out
Remove-Item Env:NXRECOMP_OPT
```
