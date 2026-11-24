# FagriShot

**English** · [العربية](README.ar.md)

Play *Fire Emblem: Fortune’s Weave* on Windows using files extracted from your own legally owned copy of the game. FagriShot supports the tested Nintendo Switch 2 update **131072** and its matching merged RomFS.

**[Download FagriShot for Windows x64](https://github.com/thunderq10/FagriShot/releases/latest/download/FagriShot-Windows-x64.zip)**

## Start playing

1. Extract the entire ZIP to a folder you can write to. Open **`FagriShotLauncher.exe`**.
2. Select your **ExeFS** folder (the one containing `main` and `main.npdm`) and your **RomFS** folder (the game content with update 131072 applied).
3. Click **Build game code** and wait for it to finish. The launcher checks your files, builds the game on your PC and prepares the shaders.
4. Click **Play**. On later visits, open the same launcher and click **Play** again. At the first Autosave notice, press **Enter** or **Space**.

The build tools come in the ZIP. The game files stay on your PC. Use Windows x64 with a Vulkan 1.3-capable graphics driver. Checking the game files can take several minutes.

If setup stops, the launcher shows which step needs attention. For a folder mismatch, check that both folders come from the supported update. **Local files** shows your logs, saves and backup options. [Input details](docs/inputs.md) · [Save recovery](docs/storage.md)

## Local 0.3.0 preview

**Experimental ray-traced reflections** is an optional graphics setting, off by default. It traces surfaces visible to the game camera on compatible Vulkan GPUs. Objects outside the current view are absent from the reflection geometry. **Screen-space reflections** controls the game's original effect independently.

The public download above is version 0.2.0. In the local 0.3.0 preview, open **Graphics settings** beside **Play** to change output resolution, 3D render scale, gameplay frame rate, upscaler and screen-space reflections. For native 4K, select **3840 × 2160 (4K)** and **100% (native)**. Use **Save graphics settings**, then start the game. In-game **F1 → Graphics → Live render state** shows the actual render and output sizes.

**Arabic text preview** includes 365 translated entries covering opening conversations, world-map exchanges, movie subtitles, chapter headings and selected setup menus. On the next launch, FagriShot builds the Arabic text, fonts and Autosave illustration from your verified RomFS. Other text appears in US English. Voices stay in the game’s selected audio language. Turn the preview off before a later launch to return to your usual text language.

For **NVIDIA DLSS**, download the official Streamline 2.14.16 SDK. From its `bin/x64` folder, copy `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll` and `nvngx_dlss.dll` into `upscalers/streamline` next to `FagriShotLauncher.exe`. Reopen **Graphics settings**, choose **NVIDIA DLSS** and save. A compatible RTX GPU and driver are required. [NVIDIA Streamline 2.14.16 SDK download](https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip)

## About the project

FagriShot is maintained by **thunderq10** and builds on Goldoire and contributors’ Fortune’s Weave port and drakolordx7’s nxrecomp. Local testing reached the opening chapter, mode selection and character creation. Tactical battles and campaign save/load still need verification. [Test results](docs/status.md) · [Build from source](docs/BUILDING.md) · [Upstream and credits](docs/foundation.md) · [License](LICENSE) · [Legal](LEGAL.md)
