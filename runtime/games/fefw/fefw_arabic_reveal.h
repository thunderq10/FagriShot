// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace nxrt::games::fefw::arabic {

// The overlay adds these zero-ink twins to each font's CMAP/CWDH with the
// original advance. Keep in sync with hidden_glyph() in fagrishot/arabic_font.py.
inline uint32_t hidden_glyph(uint32_t cp) {
    if (cp >= 0xfe70 && cp <= 0xfeff) return 0xf070 + cp - 0xfe70;
    if (cp >= 0x21 && cp <= 0x7e) return 0xf121 + cp - 0x21;
    if (cp >= 0x600 && cp <= 0x6ff) return 0xf200 + cp - 0x600;
    if (cp >= 0xfb50 && cp <= 0xfdff) return 0xf450 + cp - 0xfb50;
    if (cp >= 0xa1 && cp <= 0xff) return 0xf7a1 + cp - 0xa1;
    if (cp >= 0x2000 && cp <= 0x206f) return 0xf300 + cp - 0x2000;
    return 0;
}

inline void append_bmp(std::string& output, uint32_t cp) {
    output.push_back(static_cast<char>(0xe0 | (cp >> 12)));
    output.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
    output.push_back(static_cast<char>(0x80 | (cp & 63)));
}

struct RevealUnit {
    size_t offset;
    size_t size;
    uint32_t cp;
};

// Display view between stock talk steps. The entry hook restores the original
// UTF-8 bytes before the game moves its 0x11 cursor or handles the skip button.
// Fully exposed pages use leading 0x12 and trailing 0x11. Unknown glyphs and
// control formats take the original path.
inline bool project_reveal(const uint8_t* input, size_t bound, std::string& output) {
    std::vector<RevealUnit> units;
    size_t cursor = size_t(-1), end_marker = size_t(-1), pos = 0;
    bool arabic = false;
    while (pos < bound && input[pos] != 0) {
        const size_t start = pos;
        uint32_t cp = input[pos++];
        if (cp == 0x11 || cp == 0x12) {
            size_t& marker = cp == 0x11 ? cursor : end_marker;
            if (marker != size_t(-1)) return false;
            marker = units.size();
            continue;
        }
        if (cp == 0x0e || cp == 0x0f) {
            const size_t header = cp == 0x0e ? 7 : 5;
            if (header > bound - start) return false;
            const size_t extra = cp == 0x0e ? (input[start + 5] | size_t(input[start + 6]) << 8) : 0;
            if (extra > bound - start - header) return false;
            // Palette changes carry no width. Stateful or printable commands
            // must be resolved by the guest before they can use this view.
            if (cp != 0x0e || input[start + 1] != 0 || input[start + 2] != 0 ||
                input[start + 3] != 3 || input[start + 4] != 0 || extra != 2) return false;
            pos = start + header + extra;
        } else if (cp >= 0x80) {
            const int tails = cp >= 0xc2 && cp <= 0xdf ? 1 : cp >= 0xe0 && cp <= 0xef ? 2 : -1;
            if (tails < 0 || size_t(tails) > bound - pos) return false;
            cp &= tails == 1 ? 31 : 15;
            for (int i = 0; i < tails; ++i) {
                if ((input[pos] & 0xc0) != 0x80) return false;
                cp = (cp << 6) | (input[pos++] & 63);
            }
            if ((tails == 2 && cp < 0x800) || (cp >= 0xd800 && cp <= 0xdfff)) return false;
            arabic |= (cp >= 0xfe70 && cp <= 0xfefc) || (cp >= 0xfb50 && cp <= 0xfdff);
            if (cp != 0xa0 && !hidden_glyph(cp)) return false;
        } else if (cp < 32 && cp != '\n') {
            return false;
        }
        units.push_back({start, pos - start, cp});
    }
    if (pos == bound || !arabic || cursor == size_t(-1) || cursor >= end_marker || end_marker != units.size()) return false;

    output.clear();
    output.reserve(pos + 8);
    output.push_back(0x12);
    for (size_t line = 0; line < units.size();) {
        size_t end = line, total = 0, revealed = 0;
        while (end < units.size() && units[end].cp != '\n') {
            if (units[end].cp != 0x0e) {
                ++total;
                if (end < cursor) ++revealed;
            }
            ++end;
        }
        size_t glyph = 0;
        for (size_t i = line; i < end; ++i) {
            const auto& u = units[i];
            bool hide = false;
            if (u.cp != 0x0e) hide = glyph++ < total - revealed;
            if (hide && u.cp != ' ' && u.cp != 0xa0) {
                append_bmp(output, hidden_glyph(u.cp));
            } else {
                output.append(reinterpret_cast<const char*>(input + u.offset), u.size);
            }
        }
        if (end < units.size()) output.push_back('\n');
        line = end + 1;
    }
    output.push_back(0x11);
    return true;
}

} // namespace nxrt::games::fefw::arabic
