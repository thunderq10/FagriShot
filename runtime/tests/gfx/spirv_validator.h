// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdlib>
#include <filesystem>
#include <sstream>
#include <string>

namespace nxtest {

// An explicit test override is authoritative. Otherwise use the active SDK or PATH,
// with a default Windows SDK installation as a fallback for shells without either.
inline std::string find_spirv_val() {
    namespace fs = std::filesystem;
    std::error_code ec;
    if (const char* override_path = std::getenv("NXRT_SPIRV_VAL"); override_path && *override_path) {
        return fs::is_regular_file(override_path, ec) ? override_path : "";
    }
    if (const char* sdk = std::getenv("VULKAN_SDK"); sdk && *sdk) {
        const fs::path tool = fs::path(sdk) / "Bin" / "spirv-val.exe";
        if (fs::is_regular_file(tool, ec)) return tool.string();
    }
    if (const char* path = std::getenv("PATH")) {
        std::istringstream entries(path);
        for (std::string dir; std::getline(entries, dir, ';');) {
            if (dir.size() >= 2 && dir.front() == '"' && dir.back() == '"') dir = dir.substr(1, dir.size() - 2);
            if (dir.empty()) continue;
            const fs::path tool = fs::path(dir) / "spirv-val.exe";
            if (fs::is_regular_file(tool, ec)) return tool.string();
        }
    }
    fs::path best;
    for (const auto& entry : fs::directory_iterator(R"(C:\VulkanSDK)", ec)) {
        if (entry.path() > best && fs::is_regular_file(entry.path() / "Bin" / "spirv-val.exe", ec)) best = entry.path();
    }
    return best.empty() ? "" : (best / "Bin" / "spirv-val.exe").string();
}

}  // namespace nxtest
