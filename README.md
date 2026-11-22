# FagriShot

**English** · [العربية](README.ar.md)

FagriShot is thunderq10’s independently maintained native Windows x64 recompilation of *Fire Emblem: Fortune’s Weave* for Nintendo Switch 2. It translates the six executable modules from your own extracted game into a Windows executable and renders the original content through Vulkan. The GitHub repository contains the source and build tools. You supply the game files and build your own `FagriShot.exe`.

On the tested update 131072 extraction, the game accepted **Enter** at the Autosave prompt and advanced through the opening cinematic to the prologue chapter card, “The White Bird.” The playtest also reached the Casual/Classic menu and character creation. Tactical battles, enemy turns and campaign save/load still need verification. [Verification record](docs/status.md)

## Play after setup

Double-click **`work\private\game\FagriShot.exe`** inside the checkout where you built it. A Windows progress dialog appears while FagriShot checks the saved game files and the prepared shader cache. This happens on every launch. The tested RomFS alone held 252,686 files totaling 29 GB, so allow several minutes for the checks. The game window opens when they finish. At the first **Autosave** notice, press **Enter** or **Space** to continue.

Open `work\tools\FagriShotLauncher.exe` to change folder locations, run diagnostics or manage backups. If direct Play fails, **Game setup** shows the specific diagnostic. Keep both executables in their generated locations.

## First-time setup from GitHub

### 1. Prepare the Windows build tools

- Use Windows x64, a processor with SSE4.2, POPCNT and CMPXCHG16B, and a Vulkan 1.3-capable graphics driver. The verified configuration used Windows 11 and an NVIDIA GeForce RTX 4090 Laptop GPU. [Tested environment](docs/environment.md)
- Install Git, PowerShell 7, **64-bit CPython 3.13** available as `python`, and Node.js 22.12 or later with npm.
- Install Visual Studio or Visual Studio Build Tools with the **Desktop development with C++** workload, the x64 MSVC tools, a Windows SDK, and the C++ CMake tools for Windows. The build uses CMake 3.25 or later and Ninja.
- Install the Vulkan SDK and make sure its `spirv-val` and `spirv-dis` commands are on `PATH`. The setup window uses Microsoft Edge WebView2 Runtime.

Open PowerShell 7. Clone this repository and run the remaining commands from its root. The first build downloads pinned dependencies, including LLVM, Rust and the .NET shader tools. [Dependency provenance](docs/foundation.md)

```powershell
git clone https://github.com/thunderq10/FagriShot.git
cd FagriShot
python -I scripts/setup_python.py
.\tools\.venv\Scripts\python.exe -I scripts/setup_llvm.py
python -I scripts/build_shaders.py --rebuild
python -I scripts/build_launcher.py --bootstrap
```

### 2. Select your game files

Use your extracted **ExeFS** and the **merged base and update RomFS** for program `04007EF01F8AE000`, update `131072`. Select the folders containing the files themselves. ExeFS must include `main`, `sdk`, `subsdk0`, `subsdk1`, `subsdk2`, `rtld` and `main.npdm`. Keep the two input folders separate from each other and from the build outputs. [Input requirements and optional CNMT metadata](docs/inputs.md)

Replace the two example paths below with your actual directories. You can also open **Game setup** in `work\tools\FagriShotLauncher.exe`, browse to both folders and click **Save locations**.

```powershell
python -I scripts/fagrishot.py configure --exefs 'D:\GameDumps\FortunesWeave\exefs' --romfs 'D:\GameDumps\FortunesWeave\romfs'
```

### 3. Build the game and prepare it for Play

With the locations saved, run these commands in order. Let each finish successfully before starting the next.

```powershell
python -I scripts/fagrishot.py build-code --jobs 8
python -I scripts/fagrishot.py enroll-local
python -I scripts/fagrishot.py prepare --jobs 8
```

1. **Build game code** checks the executable fingerprints, prepares working copies, translates and compiles the six modules, and links `work\private\game\FagriShot.exe`. An interrupted build can be run again. Its command log is under `work\private\logs\`.
2. **Enroll the local extraction** records the exact fingerprints of your selected ExeFS and RomFS for this checkout and revision. The current public catalog identifies the executables but has no independently confirmed RomFS pairing. The launcher labels enrolled inputs **Local test pairing**. [Private enrollment](docs/inputs.md)
3. **Prepare shaders** extracts the game’s shaders, translates them to SPIR-V, and validates the results for Vulkan 1.3. The final cache is tied to the inputs, game build and shader tools. After preparation succeeds, double-click the generated game executable or use **Play** in the launcher. [Shader verification](docs/shader-verification.md)

## Diagnostics and player data

- **Play stops during startup.** Read the diagnostic in **Game setup**. From PowerShell, `python -I scripts/fagrishot.py play` runs the same file and shader checks and prints the failing stage.
- **A selected folder moved or changed.** Save its new location, check the extraction against the update profile, then run the relevant build, enrollment and shader steps again. A new source revision also requires a current build and local enrollment.
- **Find logs or back up saves.** Open **Local files**, or run `python -I scripts/fagrishot.py doctor` to see the actual paths to settings, saves and logs. The main runtime log is `FagriShot.log`. Microsoft Store Python can redirect the usual `%LOCALAPPDATA%\FagriShot` folder. [Storage and recovery](docs/storage.md)

Generated game code and libraries live under `work\private\`. The development tools ZIP supports diagnostics and save recovery. Use the Git checkout for a game build. Application updates are manual. Contributors can run the independent [CPU](docs/cpu-verification.md), [runtime](docs/runtime-verification.md), and [shader](docs/shader-verification.md) checks separately.

## Credits and license

FagriShot builds on the Fortune’s Weave port created by Goldoire, its maintainers and fork contributors, and drakolordx7’s nxrecomp work. The fork preserves the upstream commit ancestry, original game identity and component notices. [Pinned upstream source](https://github.com/metrist38/fefw-recomp/tree/ba87770fece1066f736e11e81e36b714ec56df0d)

FagriShot’s code, monogram and wordmark use GPL-3.0-or-later. The game and its content remain the property of their respective owners. Third-party components retain their original notices. [License](LICENSE) · [Runtime credits](runtime/THIRD_PARTY_NOTICES.md) · [Translator credits](recomp/THIRD_PARTY.md) · [Project provenance](docs/foundation.md)
