# Windows player package

Download the complete **FagriShot-Windows-x64.zip** for Windows 11 x64 and extract it into its own writable folder. The ZIP includes the launcher, compiler, Python, shader translator, validator and Arabic translation pack. The launcher uses Microsoft Edge WebView2, and the game requires a Vulkan 1.3-capable graphics driver. [Download the Windows player](https://github.com/thunderq10/FagriShot/releases/latest/download/FagriShot-Windows-x64.zip)

1. Open **FagriShotLauncher.exe**. Select the **ExeFS** and merged **RomFS** from your own copy of the supported Nintendo Switch 2 update **131072**.
2. Click **Build game code** to check your files, compile the game locally and prepare its shaders. Checking the folders can take several minutes.
3. For Arabic, select **Arabic text preview** in **Game setup**. Click **Play**. On later visits open the same launcher and click **Play** again.

Version 0.3.0 includes Arabic for all 64,503 visible message entries, with 2,633 editorially reviewed and 61,870 machine-translated drafts. The first Arabic launch builds the text, fonts and illustrations from your files. The guides cover native 4K, frame-rate targets, optional NVIDIA DLSS and experimental ray-traced reflections. [English player guide](../README.md) · [دليل التشغيل بالعربية](../README.ar.md)

## Saves and updates

**Local files** shows the paths to your logs, saves and backups. When updating, extract the new player into a fresh folder. Keep the previous copy until the new one is working, and use the same Windows account to access your saved games. [Save backup and recovery](storage.md)

## Packaging from source

The Windows release builder is `scripts/package_player.py`. Its `--arabic-campaign <folder>` option takes a campaign manifest and the compressed translation tables. It checks each table against its SHA-256. The extracted-player verification reads every table with the bundled Python. Reviewed entries in `fagrishot/arabic_data/*.json` take precedence when the source fingerprints match.

To build the same Arabic catalog into a player, extract the official release and pass its `fagrishot/arabic_data/campaign` folder to the builder. From a checkout with the native launcher, runtime and packaging prerequisites built, replace the example folder with your extracted player path.

```powershell
python scripts/package_player.py --arabic-campaign "D:\FagriShot\fagrishot\arabic_data\campaign"
```

To verify an existing player ZIP from a prepared source checkout, run the following command with the path to your download.

```powershell
python scripts/package_player.py --verify-only .\FagriShot-Windows-x64.zip
```

The archive includes the source revision in `SOURCE.txt` and the third-party notices in `licenses/`. [Build from source](BUILDING.md) · [Credits](foundation.md) · [License](../LICENSE)
