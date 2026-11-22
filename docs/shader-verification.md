# Windows shader translator verification

Run from the source checkout with Python, Git for Windows, and Vulkan SDK `spirv-val` and `spirv-dis` on PATH.

```powershell
python scripts/build_shaders.py --rebuild
```

The script uses two MSBuild workers by default. `--jobs N` changes this limit. The first run stages three MIT Ryubing projects at commit `a82350bb774f70fcbd41c9987bf67a3775409963`, restores the 57 versioned packages in `scripts/build_shaders.nuget.json` from NuGet, and verifies their archive SHA-512 values before compilation. It checks the exact checkout and staged sources on subsequent runs. The wrapper follows the source list in the upstream staging script and preserves existing source trees on mismatch. [Upstream staging](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/shaders/scripts/setup_ryujinx.ps1) · [Ryubing](https://git.ryujinx.app/projects/Ryubing/)

Windows x64 .NET SDK 10.0.404 is pinned. If the default SDK differs, the wrapper verifies Microsoft's SDK archive SHA-512 and installs it under `tools/shaders/build/sdk-cache/sdk`. Restore uses a project-local package cache. A clean checkout needs network access for these dependency downloads. [Microsoft release metadata](https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json)

On this host the Release build produced `tools/shaders/build/bin/Release/net10.0/spirv_harness.dll`. Keep its runtime configuration and dependency assemblies alongside it. The rebuild emitted four inherited CS0675 sign-extension warnings in the Ampere frontend. Exact diagnostic locations remain in the build output. No shader semantics were changed.

| Executed check | Result |
|---|---|
| Encoded Ampere compute program through the normal single-shader NVN2 entry point | A 32-byte original BAR.SYNC / EXIT program produced 724 bytes of SPIR-V. Disassembly preserved the compute entry point, 32 × 1 × 1 workgroup and control barrier. |
| Upstream synthetic Ampere warp programs through the decoded-instruction frontend | All 45 programs translated, including subgroup vote, shuffle, reduction, mask and match fixtures. The upstream harness checks retained subgroup operations where applicable. |
| Independent Windows SPIRV-Tools v2026.1 | All 46 modules passed `spirv-val --target-env vulkan1.3`. |

The fixture's instruction encodings and control fields are traceable to the pinned [Ampere decoder](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/shaders/ampere/decoder/SassDecoder.Ctrl.cs), [predicate fields](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/shaders/ampere/decoder/DecodeContext.cs) and [NVN2 reader](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/shaders/harness/Program.cs). The decoded fixtures enter through [WarpTests](https://github.com/metrist38/fefw-recomp/blob/ba87770fece1066f736e11e81e36b714ec56df0d/tools/shaders/harness/WarpTests.cs).

Each fixture run writes a fresh directory under `tools/shaders/build/verification/` holding the generated modules, the encoded fixture and `verification.json` with tool identities and module hashes. The first full wrapper rebuild produced `run-r7jqn1x1/verification.json` and translator SHA-256 `ecc7298f6d989633dc8fe60cf968a5b8bcb26864f74ff97650e5654e62f3dec5`.

## Local update 131072 shader results

The private update 131072 extraction yielded 45,257 shader occurrences. The 11,977 distinct shaders used by NVN comprised 7,761 vertex, 4,096 fragment, 118 compute and 2 geometry shaders. All translated, and all 9,988 distinct graphics-program tuples referenced shaders of the expected stages.

The same extraction identified 111 distinct console CUDA kernels in the `subsdk0` DLSS module. The Windows renderer handles that subsystem in its host upscaler. Of those kernels, 32 translated and 79 reported unsupported instructions. Their individual results are retained in the private shader report. The 12,009 emitted SPIR-V files passed independent Vulkan 1.3 validation with zero failures. The preparer checks both extraction tables, control descriptors and graphics-program references before classifying a kernel as host-replaced. [Runtime DLSS implementation](../runtime/src/gfx/nvn/api_dlss.h)

The local trial's `work/private/shader-trial-cy5aiwqt/independent-validation.json` identifies the translator, validator and counts. Each prepared cache contains `shader-report.json` and `ready.json` with its module and table hashes. Scene coverage is tracked in the [verification record](status.md).
