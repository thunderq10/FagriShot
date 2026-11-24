// SPDX-License-Identifier: GPL-3.0-or-later
#include "../../fefw_arabic_reveal.h"
#include "test_framework.h"

NXTEST_GROUP(fefw_arabic_reveal)

namespace {
using nxrt::games::fefw::arabic::append_bmp;
using nxrt::games::fefw::arabic::hidden_glyph;
using nxrt::games::fefw::arabic::project_reveal;

std::string glyph(uint32_t cp) {
    std::string result;
    append_bmp(result, cp);
    return result;
}

bool project(const std::string& src, std::string& dst) {
    return project_reveal(reinterpret_cast<const uint8_t*>(src.data()), src.size() + 1, dst);
}
}

NXTEST(partial_page_reveals_the_right_edge_of_each_line) {
    const std::string a = glyph(0xfeb1), b = glyph(0xfee1), c = glyph(0xfede);
    const auto hidden = [](uint32_t cp) { return glyph(hidden_glyph(cp)); };
    std::string dst;
    CHECK(project(a + '\x11' + b + "\n" + c + "!" + '\x12', dst));
    CHECK(dst == '\x12' + hidden(0xfeb1) + b + "\n" + hidden(0xfede) + hidden('!') + '\x11');
    CHECK(project(a + b + "\n" + c + '\x11' + "!" + '\x12', dst));
    CHECK(dst == '\x12' + a + b + "\n" + hidden(0xfede) + "!" + '\x11');
}

NXTEST(no_change_to_completed_non_arabic_or_malformed_input) {
    std::string dst;
    const std::string a = glyph(0xfeb1);
    CHECK(!project('\x12' + a + '\x11', dst));
    CHECK(!project(std::string("Eng") + '\x11' + "lish" + '\x12', dst));
    CHECK(!project(a + "!", dst));
    CHECK(!project(a + '\x11' + "\x0e" + '\x12', dst));
    CHECK(!project(a + '\x11' + glyph(0xe0ef) + '\x12', dst));
    CHECK(!project(a + '\x11' + '\x11' + "!" + '\x12', dst));
    const auto partial = a + '\x11' + "!" + '\x12';
    CHECK(!project_reveal(reinterpret_cast<const uint8_t*>(partial.data()), partial.size(), dst));
}

NXTEST(preserves_palette_parameters_and_original_buffer) {
    std::string dst;
    const std::string a = glyph(0xfeb1), b = glyph(0xfee1);
    const std::string color("\x0e\0\0\x03\0\x02\0\x04\0", 9);
    const std::string input = a + '\x11' + color + b + '\x12';
    const std::string original = input;
    CHECK(project(input, dst));
    CHECK(dst == '\x12' + glyph(hidden_glyph(0xfeb1)) + color + b + '\x11');
    CHECK(input == original);
}
