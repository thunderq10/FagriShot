// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <windows.h>
#include <cstdlib>
#include <filesystem>
#include <string>
#include "nxrt/config.h"

namespace fagrishot_entry {

inline bool is_file(const std::filesystem::path& path) {
    const DWORD attr = GetFileAttributesW(path.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

inline std::filesystem::path launcher_for(const std::filesystem::path& game_dir) {
    const auto adjacent = game_dir / L"FagriShotLauncher.exe";
    if (is_file(adjacent)) return adjacent;
    const auto private_dir = game_dir.parent_path();
    const auto work = private_dir.parent_path();
    if (_wcsicmp(game_dir.filename().c_str(), L"game") == 0 &&
        _wcsicmp(private_dir.filename().c_str(), L"private") == 0 &&
        _wcsicmp(work.filename().c_str(), L"work") == 0 &&
        is_file(work.parent_path() / L"compatibility" / L"supported.json") &&
        is_file(work.parent_path() / L"fagrishot" / L"__main__.py")) {
        const auto tools = work / L"tools" / L"FagriShotLauncher.exe";
        if (is_file(tools)) return tools;
    }
    return {};
}

inline std::wstring request_play() {
    std::wstring module(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, module.data(), static_cast<DWORD>(module.size()));
    if (length == 0 || length >= module.size()) return L"Open FagriShotLauncher.exe to start FagriShot.";
    module.resize(length);
    const auto launcher = launcher_for(std::filesystem::path(module).parent_path());
    if (launcher.empty())
        return L"Open FagriShotLauncher.exe from your FagriShot tools. In a source checkout, build the launcher with scripts/build_launcher.py.";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    std::wstring command = L"\"" + launcher.wstring() + L"\" --play-game";
    if (!CreateProcessW(launcher.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr,
                        launcher.parent_path().c_str(), &startup, &child)) {
        const DWORD error = GetLastError();
        return L"Windows could not open " + launcher.wstring() + L" (error " + std::to_wstring(error) + L").";
    }
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return {};
}

inline const char* prepare_save_directory(const char* ini) {
    if (!ini || !*ini || !nxrt::config_load_file(ini))
        return "Open FagriShotLauncher.exe, or pass --ini with a readable diagnostics configuration.";
    // The runtime reloads its INI before registering services. Pin the INI's explicit
    // absolute save root in this process so an ambient override or file change cannot
    // redirect a save to the inherited runtime fallback.
    if (_putenv_s("NXRT_FS_SAVE_DIR", "") != 0) return "Could not isolate the save directory.";
    const std::string save = nxrt::config_get_str("fs", "save_dir", "");
    try {
        if (!save.empty() && std::filesystem::path(std::u8string(save.begin(), save.end())).is_absolute() &&
            _putenv_s("NXRT_FS_SAVE_DIR", save.c_str()) == 0) return nullptr;
    } catch (const std::filesystem::filesystem_error&) {
        // Report invalid UTF-8 or an unusable path using the same player-facing diagnostic.
    }
    return "Set [fs] save_dir to an absolute, isolated save folder in the selected INI.";
}
}  // namespace fagrishot_entry
