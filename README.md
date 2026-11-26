# FagriShot

**English** · [العربية](README.ar.md)

Play *Fire Emblem: Fortune’s Weave* on Windows using files extracted from your own legally owned copy of the game. FagriShot supports the tested Nintendo Switch 2 update **131072** and its matching merged RomFS.

**[Download FagriShot 0.3.0 for Windows x64](https://github.com/thunderq10/FagriShot/releases/latest/download/FagriShot-Windows-x64.zip)**

## Start playing

1. Extract the entire ZIP to a folder you can write to. Open **`FagriShotLauncher.exe`**.
2. Use **Browse** to select your **ExeFS** and **RomFS** folders on any accessible drive. Alternatively, place folders named `exefs` and `romfs` beside the launcher before opening it to have them selected automatically. ExeFS contains `main` and `main.npdm`. RomFS contains the game content merged with update 131072.
3. Click **Build game code** and wait for it to finish. The launcher checks your files, builds the game on your PC and prepares the shaders.
4. Click **Play**. On later visits, open the same launcher and click **Play** again. At the first Autosave notice, press **Enter** or **Space**.

### What the laptop needs

- **Windows x64.** The tested configuration is Windows 11 x64. The ZIP includes Python, the Zig compiler and the shader tools. Extract the entire ZIP before opening the launcher, keeping its `tools` folder beside the executable.
- **Microsoft Edge WebView2 Runtime** displays the launcher. If Windows reports it missing, install Microsoft's Evergreen Runtime, then reopen FagriShot. [Microsoft WebView2 download](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)
- **A Vulkan 1.3-capable GPU and graphics driver** are required to play. If Vulkan is missing, install the current graphics driver from your laptop or GPU manufacturer. **Local files** shows the Windows Vulkan loader check and available space for the build and caches.

The first build verifies the complete game and prepares its shaders. Later launches reuse the completed build and shader cache under your Windows account. Keep your original game folders accessible. Changed inputs or a filesystem that cannot reliably identify unchanged files may need a full check again.

If setup stops, the launcher shows which step needs attention. For a folder mismatch, check that both folders come from the supported update. **Local files** shows your logs, saves and backup options. [Input details](docs/inputs.md) · [Save recovery](docs/storage.md)

## Arabic text

In **Game setup**, select **Arabic text preview** before clicking **Play**. On the first Arabic launch, FagriShot generates the translated resources from your verified RomFS. Clear the checkbox before launching to return to your usual text language. Voices use the audio language you selected.

The Arabic text for 0.3.0 covers all **64,503 visible message entries** in update 131072. **4,510 entries are in the edited catalog**, **10,110 interface entries** (names, menus, tutorials, items and skills) were retranslated from English in the latest update, and **49,883 remain first-pass machine drafts**, mostly story and support dialogue. All of this text is machine-produced. None of it has been checked by a human reviewer. Dialogue appears from right to left. The release also translates the title logo, lettering in all 316 US-English tutorial illustrations, and place names in five map films. Small lettering inside some tutorial screenshots can still appear in English.

## Graphics settings

Open **Graphics settings** beside **Play** to choose output resolution, 3D render scale, gameplay frame-rate target and upscaler. For native 4K, select **3840 × 2160 (4K)** and **100% (native)**. Click **Save graphics settings**, then start the game. In-game, **F1 → Graphics → Live render state** shows the actual render and output sizes. The 60 and 120 FPS settings are targets. Performance varies by scene and hardware.

**Experimental ray-traced reflections** is off by default. It requires a Vulkan GPU with hardware ray-query support and traces surfaces visible to the current camera. **Screen-space reflections** controls the game's original reflection effect separately.

For **NVIDIA DLSS**, download the official Streamline 2.14.16 SDK. From its `bin/x64` folder, copy `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll` and `nvngx_dlss.dll` into `upscalers/streamline` next to `FagriShotLauncher.exe`. Reopen **Graphics settings**, choose **NVIDIA DLSS** and save. A compatible RTX GPU and driver are required. [NVIDIA Streamline 2.14.16 SDK download](https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip)

## About the project

FagriShot is maintained by **thunderq10** and builds on Goldoire and contributors’ Fortune’s Weave port and drakolordx7’s nxrecomp. Local testing reached the opening chapter, mode selection and character creation. Tactical battles and campaign save/load still need verification. [Test results](docs/status.md) · [Build from source](docs/BUILDING.md) · [Upstream and credits](docs/foundation.md) · [License](LICENSE) · [Legal](LEGAL.md)
