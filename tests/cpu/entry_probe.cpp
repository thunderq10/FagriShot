// SPDX-License-Identifier: GPL-3.0-or-later
// Native fixture for the production direct-entry and pre-init save configuration.
#include "fagrishot_entry.h"
#include <cstdio>
#include <fstream>

int main(int argc, char** argv) {
    if (std::filesystem::path(argv[0]).filename() == "FagriShotLauncher.exe") {
        // The harness copies this binary to the launcher location. Verify the exact Play request and cwd.
        if (argc != 2 || std::string(argv[1]) != "--play-game") return 2;
        std::ofstream("launcher-started.txt") << argv[1];
        return 0;
    }
    if (argc == 1) {
        auto error = fagrishot_entry::request_play();
        if (error.empty()) return 0;
        std::fwprintf(stderr, L"%ls\n", error.c_str());
        return 2;
    }
    if (argc != 3) return 1;
    if (auto error = fagrishot_entry::prepare_save_directory(argv[2])) {
        std::fprintf(stderr, "%s\n", error);
        return 2;
    }
    nxrt::config_load_string("[fs]\nsave_dir=should-never-win\n");
    std::puts(nxrt::config_get_str("fs", "save_dir", "").c_str());
    return 0;
}
