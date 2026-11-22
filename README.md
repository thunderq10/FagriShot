# FagriShot

FagriShot is thunderq10’s independently maintained static recompilation project for *Fire Emblem: Fortune’s Weave* on Nintendo Switch 2. Its first Windows x64 development build includes a native launcher, the title-specific AArch64 translator, a compiled compatibility runtime, a Vulkan renderer and an Ampere-to-SPIR-V shader toolchain.

**Current milestone: M0.** The host tools build and execute on Windows 11. Genuine title boot, tactical combat, save/load and campaign coverage are awaiting locally supplied game modules and matching content. The [verification record](docs/status.md) distinguishes fixture results from game results.

## Run on Windows

From this checkout, open `work/tools/FagriShotLauncher.exe`. The launcher uses the adjacent source or tooling package. Its **Game setup** screen accepts extracted ExeFS and merged RomFS folders. **Local files** shows settings, saves, backups and diagnostic actions. All application updates are manual.

The default input locations are `exefs/` and `romfs/` in the project folder. The upstream executable profile is for program `04007EF01F8AE000`, update `131072`. M1 requires the six NSOs named `main`, `sdk`, `subsdk0`, `subsdk1`, `subsdk2`, `rtld` and the descriptor `main.npdm`. The build accepts the upstream-catalogued raw or prepared main. The complete input profile and metadata options are in [Input verification](docs/inputs.md).

```powershell
python -I scripts/fagrishot.py doctor
python -I scripts/fagrishot.py inspect --exefs .\exefs --romfs .\romfs
python -I scripts/fagrishot.py build-code --exefs .\exefs --jobs 8
```

The final command builds the six private native libraries and links `work/private/game/FagriShot.exe` once the executable inputs pass verification. An absent or changed input produces a diagnostic. No generated game executable is in the current tooling package.

## Rebuild the tools

Run from the FagriShot root in PowerShell 7 on Windows x64 with Visual Studio C++ and the Windows SDK. The validated host also has x64 Python 3.13, Node 26, LLVM and a Vulkan SDK. The build scripts stage additional dependencies locally. See the [host inventory](docs/environment.md) for versions and the [foundation review](docs/foundation.md) for pins and licenses.

```powershell
python -I scripts/setup_python.py
.\tools\.venv\Scripts\python.exe -I scripts/setup_llvm.py
python -I scripts/verify_cpu.py
pwsh -NoProfile -File .\scripts\verify_cpu_graph.ps1
pwsh -NoProfile -File .\scripts\build_runtime.ps1 -Test -Jobs 8
python -I scripts/build_shaders.py --rebuild
python -I scripts/build_launcher.py --bootstrap
node scripts/verify_launcher.mjs
python -m unittest discover -s tests -p "test_*.py" -v
```

The Python, LLVM, Rust and .NET setup steps use project-local directories. CPU fixtures and tagged GPU scenes can run without game files. Absolute paths to the latest local outputs are printed by the build tools.

## Game build and player data

The CLI and launcher use the same intake and build functions. The original modules stay in the input folder. The prepared module copy, generated C++, compiled libraries and private executable live under `work/private/`. CMake and Ninja track the module, translator, game configuration and compiler dependencies. An interrupted game build has to pass the checks and link again before Play can use it. Stage logs live under `work/private/logs/`.

`inspect` records a private inventory and checks the selected ExeFS and RomFS against the compatibility catalog. The upstream publishes an executable fingerprint. Its lack of a full matched RomFS digest is recorded in the catalog. A maintainer can validate a matched extraction and record its exact content identity. The private `prepare` and `play` commands then check the pairing, build record and shader cache. Run `python -I scripts/fagrishot.py --help` for the command list.

FagriShot settings, saves, backups, logs, mods and caches have their own locations. See [storage and recovery](docs/storage.md). The [feature and gameplay matrix](docs/status.md) covers the current verification limits.

## Credits

FagriShot builds on the Fortune’s Weave port created by Goldoire, its maintainers and fork contributors, and drakolordx7’s nxrecomp work. The selected upstream source is [metrist38/fefw-recomp at ba87770f](https://github.com/metrist38/fefw-recomp/tree/ba87770fece1066f736e11e81e36b714ec56df0d). The fork preserves the upstream commit ancestry, original game identity and component notices. FagriShot’s code, monogram and wordmark use GPL-3.0-or-later. The game and its content remain the property of their respective owners. See [LICENSE](LICENSE), [runtime credits](runtime/THIRD_PARTY_NOTICES.md), [translator credits](recomp/THIRD_PARTY.md) and [provenance](docs/foundation.md).
