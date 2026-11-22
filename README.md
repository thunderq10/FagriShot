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

## About the project

FagriShot is maintained by **thunderq10** and builds on Goldoire and contributors’ Fortune’s Weave port and drakolordx7’s nxrecomp. Local testing reached the opening chapter, mode selection and character creation. Tactical battles and campaign save/load still need verification. [Test results](docs/status.md) · [Build from source](docs/BUILDING.md) · [Upstream and credits](docs/foundation.md) · [License](LICENSE) · [Legal](LEGAL.md)
