# SPDX-License-Identifier: GPL-3.0-or-later
# Configure the production lift and link graphs in fresh directories. Build host objects only.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root ('work/cpu/graph-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $build | Out-Null
Start-Transcript -Path (Join-Path $build 'verification.log') | Out-Null
try {
    $finder = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    $vs = & $finder -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$vs) { throw 'Visual Studio with x64 C++ tools is required.' }
    Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
    Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
    $env:NX_LLVM = (Resolve-Path (Join-Path $root 'work/toolchain/llvm-23.1.5/bin')).Path
    $env:NX_LLVM_BIN = $env:NX_LLVM
    $env:PATH = $env:NX_LLVM + ';' + $env:PATH
    $env:FEFW_CPU_VARIANT = 'compat'
    Remove-Item Env:FEFW_SITES, Env:NXRECOMP_OPT -ErrorAction SilentlyContinue
    $cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
    if (!$cmake) { $cmake = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe' }
    $ninja = (Get-Command ninja -ErrorAction SilentlyContinue).Source
    if (!$ninja) { $ninja = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe' }
    $common = @('-G', 'Ninja', "-DCMAKE_MAKE_PROGRAM:FILEPATH=$ninja", '-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_NINJA_FORCE_RESPONSE_FILE=ON', "-DCMAKE_TOOLCHAIN_FILE=$root/recomp/cmake/clang-cl.cmake")
    Write-Output "Fresh build directories under $build"
    Write-Output "Visual Studio $vs, MSVC $env:VCToolsVersion, Windows SDK $env:WindowsSDKVersion"
    & (Join-Path $env:NX_LLVM 'clang-cl.exe') --version
    & $cmake --version
    & $ninja --version
    $lift = Join-Path $build 'libs'
    & $cmake -S "$root/recomp/games/fefw" -B $lift @common "-DFEFW_EXEFS=$build/exefs" "-DFEFW_GEN=$build/generated" -DFEFW_LIFT_JOBS=2 2>&1 | Tee-Object (Join-Path $build 'configure-lift.log')
    if ($LASTEXITCODE) { throw 'Six-module CMake configure failed.' }
    $graph = (& $ninja -C $lift -t query fefw_modules) -join "`n"
    if ($LASTEXITCODE) { throw 'Ninja could not query the module target.' }
    Write-Output $graph
    $modules = 'main','sdk','subsdk0','subsdk1','subsdk2','rtld'
    foreach ($module in $modules) {
        if ($graph -notmatch [regex]::Escape("fefw_$module.lib")) { throw "Missing library in lift graph: $module" }
    }
    $missing = (& $ninja -C $lift -j1 fefw_modules 2>&1) -join "`n"
    if ($LASTEXITCODE -eq 0 -or $missing -notmatch "exefs[/\\]main.*missing") {
        throw "Unexpected result for the empty fixture input directory: $missing"
    }
    $missing | Set-Content -LiteralPath (Join-Path $build 'missing-input.log')
    $game = Join-Path $build 'game'
    $settings = @("-DFEFW_LIB_DIR=$lift", "-DNXRT_DEPS_DIR=$build/vendor-downloads", '-DNXRT_ASTC2BC7=ON')
    $nasm = Join-Path $root 'work/runtime-dependencies/nasm-3.01/nasm.exe'
    if (Test-Path -LiteralPath $nasm) { $settings += "-DNXRT_NASM=$nasm" }
    & $cmake -S "$root/game-build/fefw" -B $game @common @settings 2>&1 | Tee-Object (Join-Path $build 'configure-game.log')
    if ($LASTEXITCODE) { throw 'Game CMake configure failed.' }
    $graph = (& $ninja -C $game -t query FagriShot.exe) -join "`n"
    if ($LASTEXITCODE) { throw 'Ninja could not query the game target.' }
    Write-Output $graph
    foreach ($module in $modules) {
        if ($graph -notmatch [regex]::Escape("fefw_$module.lib")) { throw "Missing library in game link graph: $module" }
    }
    $objects = @('CMakeFiles/fefw.dir/src/fefw_main.cpp.obj','CMakeFiles/fefw.dir/src/fefw_hle.cpp.obj','CMakeFiles/fefw.dir/res/fefw.rc.res')
    & $cmake --build $game --target @objects --parallel 2 2>&1 | Tee-Object (Join-Path $build 'objects.log')
    if ($LASTEXITCODE) { throw 'Game host objects failed to build.' }
    foreach ($object in $objects) {
        $file = Get-Item -LiteralPath (Join-Path $game $object)
        Write-Output "$($file.Name) $($file.Length) bytes"
    }
    Write-Output "PASS six-module configuration and host objects. Evidence: $build"
} finally {
    Stop-Transcript | Out-Null
}
