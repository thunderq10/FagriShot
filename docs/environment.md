# Windows verification host

Measured on November 22, 2026. Windows Management Instrumentation reported MSI Titan GT77HX 13VI hardware. The build shell runs in Windows desktop session 2 alongside Explorer, with a native x64 process, an interactive window station, and direct access to the GPU and the checkout. Windows reports a hypervisor present. These observations establish where compilation and GPU checks ran. They do not establish how the user connected to the desktop.

| Resource | Observation |
| --- | --- |
| Operating system | Windows 11 Pro x64, build 26300 |
| CPU | Intel Core i9-13980HX, 24 physical cores and 32 logical processors |
| RAM | 127.7 GiB physical, about 105 GiB available during inspection |
| Storage | Workspace on C: with about 3.10 TiB free |
| Display | NVIDIA GeForce RTX 4090 Laptop GPU at 3840 × 2160. Windows also enumerated Intel UHD Graphics. |
| Vulkan | `vulkaninfo --summary` exited successfully. Loader and NVIDIA physical device reported Vulkan 1.4.357, driver 621.24. The Win32 surface extension and Khronos validation layer were present. Only the NVIDIA adapter appeared in Vulkan enumeration. The loader warned about duplicate OBS layer registrations. |
| Native build | Visual Studio 2022 Community and Build Tools 17.14, VS 2026 Community 18.12, LLVM clang-cl 20.1.0. The VS 2022 installation used for the runtime provides CMake 3.31.6-msvc6 and Ninja 1.12.1. VS 2026 provides CMake 4.3.1-msvc1 and Ninja 1.13.2. Windows SDK include directories include 10.0.26100.0. |
| Other tools | Git 2.56.1.windows.1, Python 3.13.14, Tk 8.6, Go 1.27.0, .NET SDK 9.0.321, Vulkan SDK 1.4.341.1 and SPIRV-Tools 2026.1. |
| Debugging and inspection | VS native debugger components and REA CLI 6.9.0 installed. Standalone LLVM LLDB fails to start here (0xc0000135; liblldb imports python310.dll). Ghidra 12.0.4 PUBLIC is installed beside the workspace at `C:\Users\User\Desktop\ghidra_12.0.4_PUBLIC`. Its `support/analyzeHeadless.bat` exists and `Ghidra/application.properties` specifies Java 21 minimum. Temurin OpenJDK 21.0.13 is installed. |

Before integration the project directory was empty. No game location was supplied with the request, configured through the project's environment variables, or present under the inherited `game/dump/exefs` and `game/dump/romfs` defaults. The FagriShot LocalAppData directory did not exist at inspection. Other user directories or drives were not searched for game files. Tool paths came from PATH, VS Installer, SDK environment variables, standard installation paths and the adjacent Ghidra installation.

To refresh the main hardware checks in PowerShell:

```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem | Select-Object Model, TotalPhysicalMemory
Get-PSDrive -Name C | Select-Object Root, Free
vulkaninfo --summary
```
