#requires -Version 7.0
# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param(
    [ValidateRange(1, 128)]
    [int]$Jobs = [Math]::Max(1, [Math]::Min(8, [Environment]::ProcessorCount)),
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,40}$')]
    [string]$BuildName = 'release',
    [switch]$Test
)
$ErrorActionPreference = 'Stop'
$root = (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..')).FullName
$build = Join-Path $root 'work\runtime-release'
if ($BuildName -ne 'release') { $build += "-$BuildName" }
$downloads = Join-Path $root 'work\runtime-dependencies'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Install Visual Studio C++ Build Tools and its Windows SDK.' }
$vsArgs = @('-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath')
$vs = & $vswhere @vsArgs -version '[17.0,18.0)'
if (-not $vs) { $vs = & $vswhere @vsArgs }
if (-not $vs) { throw 'No Visual Studio installation with x64 C++ tools was found.' }
$llvm = Get-Command clang-cl.exe -ErrorAction SilentlyContinue
if (-not $llvm) { throw 'Install LLVM and put its bin directory on PATH.' }
& (Join-Path $vs 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
$vsCMake = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake'
function Find-BuildTool($name, $bundled) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $path = Join-Path $vsCMake $bundled
    if (Test-Path -LiteralPath $path) { return $path }
    throw "Install $name or the Visual Studio C++ CMake tools."
}
$cmake = Find-BuildTool 'cmake.exe' 'CMake\bin\cmake.exe'
$ninja = Find-BuildTool 'ninja.exe' 'Ninja\ninja.exe'
$ctest = Join-Path (Split-Path -Parent $cmake) 'ctest.exe'
$testFlag = if ($Test) { 'ON' } else { 'OFF' }
$targets = @('fefw_rt')
if ($Test) { $targets += @('nxrt_tests','nxrt_gfx_tests') }
New-Item -ItemType Directory -Path $build, $downloads -Force | Out-Null
$lockFile = Join-Path $downloads 'runtime-build.lock'
try {
    $lock = [IO.File]::Open($lockFile, 'OpenOrCreate', 'ReadWrite', 'None')
} catch [IO.IOException] {
    throw "Cannot lock $lockFile. Check for another runtime build and retry. $($_.Exception.Message)"
}
try {
    & $cmake "-DDOWNLOAD_DIR=$downloads" -P (Join-Path $PSScriptRoot 'build_runtime_tools.cmake')
    if ($LASTEXITCODE -ne 0) { throw 'Fetching the pinned NASM assembler failed.' }
    $nasm = Join-Path $downloads 'nasm-3.01\nasm.exe'
    & (Join-Path $root 'tools\textures\scripts\fetch_third_party.ps1') -CppOnly
    & $cmake -S (Join-Path $root 'game-build\fefw') -B $build -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" `
        "-DCMAKE_C_COMPILER=$($llvm.Source)" "-DCMAKE_CXX_COMPILER=$($llvm.Source)" -DCMAKE_BUILD_TYPE=Release `
        -DFEFW_RT_DLL=ON -DNXRT_ASTC2BC7=ON "-DNXRT_DEPS_DIR=$downloads" "-DNXRT_BUILD_TESTS=$testFlag" `
        "-DNXRT_NASM=$nasm" -DNXRT_DAV1D=ON -DNXRT_DAV1D_ASM=ON -DNXRT_BUILD_LIFT_TESTS=OFF -DCMAKE_TLS_VERIFY=ON
    if ($LASTEXITCODE -ne 0) { throw "Runtime configure failed ($LASTEXITCODE)." }
    & $cmake --build $build --parallel $Jobs --target @targets
    if ($LASTEXITCODE -ne 0) { throw "Runtime build failed ($LASTEXITCODE)." }
    if ($Test) {
        $dll = Join-Path $build 'fefw_rt.dll'
        $library = [Runtime.InteropServices.NativeLibrary]::Load($dll)
        try {
            $exports = Get-Content -LiteralPath (Join-Path $root 'game-build\fefw\src\fefw_rt.def') |
                ForEach-Object { if ($_ -match '^\s+([A-Za-z_]\w*)(?:\s+DATA)?\s*$') { $Matches[1] } }
            if ('fefw_rt_main' -notin $exports) { throw 'Missing runtime entry in the export definition.' }
            foreach ($symbol in $exports) {
                $pointer = [IntPtr]::Zero
                if (-not [Runtime.InteropServices.NativeLibrary]::TryGetExport($library, $symbol, [ref]$pointer)) {
                    throw "The Windows loader could not resolve runtime export $symbol."
                }
            }
            Write-Host "Windows loaded fefw_rt.dll and resolved $($exports.Count) ABI exports."
        } finally {
            [Runtime.InteropServices.NativeLibrary]::Free($library)
        }
        $groups = @(
            'loader','nxmodule','runtime_e2e','eh','eh_abi3','hipc','ipc','kernel_lightevent','kernel_sync','svc_memory',
            'svc_acc','svc_fs','svc_fs_romfs','svc_fs_volume','svc_hid','svc_time','svc_audio_out','svc_audio_sink_opts',
            'svc_audren_dec','svc_audren_mix','media_vdec','play_pad','texture_pool_readiness',
            'gfx_nvn_abi','gfx_nvn_format','gfx_nvn_astc_gpu','gfx_nvn_scene_clear','gfx_scene'
        )
        $pattern = '^(' + ($groups -join '|') + ')$'
        $oldTemp = $env:TEMP
        $oldTmp = $env:TMP
        $oldUpdateRef = $env:NXRT_GFX_UPDATE_REF
        try {
            $env:TEMP = $env:TMP = (New-Item -ItemType Directory -Force -Path (Join-Path $build 'test-temp')).FullName
            $env:NXRT_GFX_UPDATE_REF = '0'
            & $ctest --test-dir $build --no-tests=error --output-on-failure -R $pattern
            if ($LASTEXITCODE -ne 0) { throw "Runtime fixture verification failed ($LASTEXITCODE)." }
        } finally {
            $env:TEMP = $oldTemp
            $env:TMP = $oldTmp
            $env:NXRT_GFX_UPDATE_REF = $oldUpdateRef
        }
    }
} finally {
    $lock.Dispose()
}
