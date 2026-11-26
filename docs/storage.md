# Storage and recovery

The normal Windows namespace is `%LOCALAPPDATA%\FagriShot\`. The source checkout and application package may be replaced independently of these folders.

With Microsoft Store Python, Windows can redirect this folder into the Python package's local cache. **Local files** and `python -I scripts/fagrishot.py doctor` show the physical paths passed to the native game.

| Folder | Contents |
| --- | --- |
| build | Compiled game and prepared executable modules for the Windows player. Compatible installs under the same Windows account reuse this folder. |
| settings | Selected input paths in `launcher.json`, runtime `fefw.ini`, controls and a save-operation lock. |
| saves | The game’s local save tree. The launcher provides this root explicitly at launch. |
| backups | Timestamped snapshots with per-file SHA-256 digests. Retention keeps the ten most recent verified snapshots. It also preserves damaged or unrecognized backup folders for inspection. |
| caches | Shader corpus, validated SPIR-V, runtime pipeline and texture cache. A new identity gets a different namespace. |
| logs | Input fingerprints and the main runtime `FagriShot.log`. Runtime exception and hang reports default to the private game executable directory. |
| mods | Reserved product namespace. Baseline launch disables file overrides and native mod DLLs. |

On Linux the CLI maps configuration to `$XDG_CONFIG_HOME/fagrishot`, saves/backups/mods to `$XDG_DATA_HOME/fagrishot`, logs to `$XDG_STATE_HOME/fagrishot` and caches to `$XDG_CACHE_HOME/fagrishot`, with the standard home-directory defaults. Native Linux and Proton gameplay both await separate verification. The current game build and the distributed launcher target Windows x64.

The launcher creates its profile directories on first use. Before a managed play session, it takes a snapshot of the saves or verifies that the latest backup already contains the same files. It also snapshots the current saves before restoring a backup. Empty initial directories have no snapshot. The following commands operate on the current FagriShot profile. Close any independently started game process first.

```powershell
python -I scripts/fagrishot.py backup
python -I scripts/fagrishot.py backups
python -I scripts/fagrishot.py restore <backup-id>
```

A restore verifies the source backup, stages a complete copy and switches directories with same-volume renames. The next save operation recovers an interrupted switch. The snapshot tests use disposable files. Existing upstream saves and settings require an independently validated, compatible game format before import. Ordinary FagriShot backup and restore operate only on its own save directory.

The launcher saves the selected paths when **Build game code** verifies both folders. If Game setup reports damaged settings, select both folders and click **Build game code** again. Backup and restore remain available under **Local files** while you repair the locations. Engine technical names including `fefw.ini`, exported `nx_*` symbols, `fefw_rt.dll`, NSO module names and title IDs remain stable. The registered game title remains the original title. FagriShot owns the host window title, game/launcher file names and default log.

To update, download the Windows player ZIP, extract it into a new folder, open its launcher and select **Build game code**. The new copy uses the same player settings, saves and backups. Keep the previous folder until you have tested the new build. [GitHub Releases](https://github.com/thunderq10/FagriShot/releases)
