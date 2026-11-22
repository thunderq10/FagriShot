#requires -Version 7.0
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the package's validator with a static CRT so players need only Windows.
$ErrorActionPreference = 'Stop'
$root = (Get-Item (Join-Path $PSScriptRoot '..')).FullName
$base = (New-Item -ItemType Directory -Force (Join-Path $root 'work/player-deps')).FullName
$source = Join-Path $base 'spirv-tools'
$build = Join-Path $base 'spirv-static'
function Sync-Source($folder, $repo, $revision) {
    if (-not (Test-Path (Join-Path $folder '.git'))) { git init -q $folder }
    git -C $folder cat-file -e "$revision^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        git -c http.sslBackend=schannel -C $folder fetch -q --depth=1 $repo $revision
        if ($LASTEXITCODE -ne 0) { throw "Could not fetch $repo at $revision" }
    }
    git -C $folder checkout -q --detach $revision
    if ($LASTEXITCODE -ne 0 -or (git -C $folder rev-parse HEAD) -ne $revision -or
        (git -C $folder status --porcelain --untracked-files=no)) { throw "Unexpected source in $folder" }
}
Sync-Source $source 'https://github.com/KhronosGroup/SPIRV-Tools.git' 'fbe4f3ad913c44fe8700545f8ffe35d1382b7093'
Sync-Source (Join-Path $source 'external/spirv-headers') 'https://github.com/KhronosGroup/SPIRV-Headers.git' '04f10f650d514df88b76d25e83db360142c7b174'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'The release builder needs Visual Studio C++ and CMake tools.' }
& (Join-Path $vs 'Common7/Tools/Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$tools = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake'
$cmake = Join-Path $tools 'CMake/bin/cmake.exe'
$ninja = Join-Path $tools 'Ninja/ninja.exe'
& $cmake -S $source -B $build -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_POLICY_DEFAULT_CMP0091=NEW -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded `
    -DSPIRV_SKIP_TESTS=ON -DSPIRV_TOOLS_BUILD_STATIC=ON -DBUILD_SHARED_LIBS=OFF `
    '-DCMAKE_C_FLAGS_RELEASE=/O2 /DNDEBUG /MT /Brepro' '-DCMAKE_CXX_FLAGS_RELEASE=/O2 /DNDEBUG /MT /Brepro' `
    '-DCMAKE_EXE_LINKER_FLAGS_RELEASE=/INCREMENTAL:NO /Brepro'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $cmake --build $build --target spirv-val --parallel 8
exit $LASTEXITCODE
