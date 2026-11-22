# FagriShot game inputs

Select the extracted ExeFS and the effective, unmodified RomFS for the same release. Supply the directories containing the actual entries. The intake only opens explicitly selected inputs, reads files in chunks, and rejects links, reparse points, overlapping roots, empty roots and unreadable entries. Input directories should be stationary during inspection. Keep reports, caches and working copies outside both input directories.

The pinned upstream profile identifies program `04007EF01F8AE000`, update `131072`, with a 20-byte main build ID of `db93b0125f4850601d61c3edde012f8eb2ecfe77`. FagriShot records all 32 bytes of every observed NSO build ID. It requires exact SHA-256 values for `main`, `sdk`, `subsdk0`, `subsdk1`, `subsdk2`, `rtld` and `main.npdm`. It recognizes the upstream raw and prepared hashes of `main`. The profile and values come from the [upstream dump catalog](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/game-build/release/dump_hashes.json) and the [per-game configuration](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/recomp/games/fefw/config.py).

**Executable inspection.** Each root-level NSO0 entry contributes its module name, build ID, SHA-256, file size, flags and segment extents. The reader checks stored byte ranges and distinguishes uncompressed, LZ4 and ZBIC segments. Fingerprinting needs neither a decompressor nor console keys. The upstream preparation step writes an uncompressed copy of the ZBIC main and checks enabled segment hashes. `main.npdm` contributes its complete hash, ACI0 program ID and raw NPDM version field. A missing descriptor permits inventory and blocks executable matching. The base/update title versions require separate evidence. [NSO and NPDM layout](https://github.com/Atmosphere-NX/Atmosphere/blob/3e8915011e1de89f595462d929c1cc3294470896/libraries/libstratosphere/include/stratosphere/ldr/ldr_types.hpp) · [Upstream preparation](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/nso/nso_unpack.py)

**Content metadata.** The optional `base_cnmt` and `update_cnmt` arguments accept raw, extracted packaged Application and Patch CNMT files. They provide the application/patch association, raw numeric release versions, content count, platform byte, file size and hash. The reader checks record extents, the trailing digest and cross-references between both supplied descriptors. Missing metadata appears as JSON null. A recorded CNMT does not establish that a directory was extracted from its listed content archives. [CNMT structures](https://github.com/Atmosphere-NX/Atmosphere/blob/3e8915011e1de89f595462d929c1cc3294470896/libraries/libstratosphere/include/stratosphere/ncm/ncm_content_meta.hpp) · [CNMT content types](https://github.com/Atmosphere-NX/Atmosphere/blob/3e8915011e1de89f595462d929c1cc3294470896/libraries/libstratosphere/include/stratosphere/ncm/ncm_content_meta_type.hpp)

**Directory identity.** Each root gets a SHA-256 over this deterministic sequence. Start with UTF-8 `FagriShot extracted tree v1` and a zero byte. Visit children depth first in UTF-8 basename order. For each child, write ASCII `D` or `F`, its relative path's length as an unsigned 64-bit big-endian integer, then the exact UTF-8 path with slash separators. For files, append its byte count in the same integer format and the 32 raw SHA-256 bytes. Empty directories contribute entries. The analysis reads all RomFS file bytes, so the first inventory of a large extraction can take time. Reports contain aggregate RomFS counts and digests without asset names, absolute source paths or file contents.

**Public compatibility.** `check_executable` returns an exact candidate for further local translation work. `check_support` additionally requires a matching record in `compatibility/supported.json` that binds the candidate ID, ExeFS and RomFS tree digests, and optional base/update metadata hashes. The upstream catalog has no RomFS digest. The shipped supported list is empty. A public entry requires independent evidence for the content pairing.

**Testing your own pairing.** In a source checkout, run the following command to enroll the configured folders for a private play test.

```powershell
python scripts/fagrishot.py enroll-local
```

Enrollment checks all executable modules, the program descriptor and any supplied CNMTs. It reads the complete ExeFS and RomFS and checks that the RomFS has the 252,686 files upstream recorded for the merged base and update 131072. A matching count can corroborate the extraction layout. Different content can have the same count. [Upstream file count](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/game-build/fefw/src/fefw_main.cpp#L369-L371)

The private receipt lives in `work/private/local-trial.json`. It records the two complete tree digests, counts and byte totals, program and build identities, optional CNMT hashes, a fingerprint of the selected paths and checkout location, and the checkout's commit and tracked code changes. `status --json` reports `local_trial` for a receipt recorded for the current locations and source. Prepare and play inspect the selected files again and require an exact match to use that receipt. Moving either folder, changing its contents or changing the source revision requires fresh enrollment. Removing the receipt ends enrollment. A local trial records the user's decision to test these inputs and makes no claim about gameplay milestones or public release support.

Run the independent synthetic regression suite from the repository root.

```powershell
python -m unittest discover -s tests -p test_inputs.py -v
```
