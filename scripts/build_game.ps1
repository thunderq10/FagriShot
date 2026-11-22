# Internal stage for python -m fagrishot build-code. Keeps the original game files read-only.
param([Parameter(Mandatory)][ValidateSet('Lift','Link')] [string]$Stage,
      [ValidateRange(1,32)][int]$Jobs=8)
$ErrorActionPreference='Stop'
$root = Split-Path -Parent $PSScriptRoot
$private = Join-Path $root 'work/private'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$vs) { throw 'Visual Studio x64 C++ tools are required.' }
Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (!$cmake) { $cmake = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe' }
$ninja = (Get-Command ninja -ErrorAction SilentlyContinue).Source
if (!$ninja) { $ninja = Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe' }
$pinned = Join-Path $root 'work/toolchain/llvm-23.1.5/bin'
$llvm = if ($env:NX_LLVM) { $env:NX_LLVM } elseif (Test-Path (Join-Path $pinned 'clang-cl.exe')) {
    $pinned
} else { Split-Path -Parent (Get-Command clang-cl).Source }
$env:NX_LLVM = $llvm
$env:NX_LLVM_BIN = $llvm
$env:PATH = $llvm + ';' + $env:PATH
$llvmVersion = & (Join-Path $llvm clang-cl.exe) --version | Select-Object -First 1
if ($llvmVersion -notmatch 'version (\d+)' -or [int]$Matches[1] -lt 23) {
    throw "The upstream full-game build requires LLVM 23 or newer. NX_LLVM currently selects $llvmVersion."
}
$env:FEFW_CPU_VARIANT = 'compat'
$env:PYTHONUTF8 = '1'
Remove-Item Env:FEFW_SITES, Env:NXRECOMP_OPT -ErrorAction SilentlyContinue
$common = @('-G','Ninja',"-DCMAKE_MAKE_PROGRAM:FILEPATH=$ninja",'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_NINJA_FORCE_RESPONSE_FILE=ON')
if ($Stage -eq 'Lift') {
    $out = Join-Path $private 'libs'
    # Ninja needs build-relative object paths when cmd.exe sees an ampersand in this checkout.
    $generated = if ($root.Contains('&')) { Join-Path $out 'gen' } else { Join-Path $private 'generated' }
    $target = 'fefw_modules'
    $src = Join-Path $root 'recomp/games/fefw'
    $opts = @("-DCMAKE_TOOLCHAIN_FILE=$root/recomp/cmake/clang-cl.cmake","-DNX_ROOT=$root",
        "-DFEFW_EXEFS=$private/prepared-exefs","-DFEFW_GEN=$generated","-DFEFW_LIFT_JOBS=$Jobs")
} else {
    $out = Join-Path $private 'game'
    $target = 'fefw'
    $src = Join-Path $root 'game-build/fefw'
    $downloads = Join-Path $root 'work/runtime-dependencies'
    & $cmake "-DDOWNLOAD_DIR=$downloads" -P (Join-Path $PSScriptRoot 'build_runtime_tools.cmake')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $opts = @("-DFEFW_LIB_DIR=$private/libs","-DCMAKE_C_COMPILER:FILEPATH=$llvm/clang-cl.exe",
        "-DCMAKE_CXX_COMPILER:FILEPATH=$llvm/clang-cl.exe","-DCMAKE_RC_COMPILER:FILEPATH=$llvm/llvm-rc.exe",
        "-DNXRT_NASM=$downloads/nasm-3.01/nasm.exe","-DNXRT_DEPS_DIR=$downloads",'-DCMAKE_TLS_VERIFY=ON',
        '-DNXRT_DAV1D=ON','-DNXRT_DAV1D_ASM=ON','-DNXRT_ASTC2BC7=ON')
}
& $cmake -S $src -B $out @common @opts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $cmake --build $out --target $target --parallel $Jobs
exit $LASTEXITCODE
