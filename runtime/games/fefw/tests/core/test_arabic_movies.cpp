// SPDX-License-Identifier: GPL-3.0-or-later
#include "test_framework.h"
#include "../../fefw_arabic_movies.h"

NXTEST_GROUP(arabic_movies)
using namespace nxrt::games::fefw::movie_labels;

NXTEST(map_label_ink_moves_and_scales_between_source_timestamps) {
    Track t{1, 1, false, {{0, 2, 2, 2, 2}, {200, 6, 2, 6, 2}}, {0, 0, 0, 255}};
    std::vector<uint8_t> nv(16 * 8 * 3 / 2, 128);
    auto original = nv;
    draw_track(nv.data(), 16, 8, t, 201);
    CHECK(nv == original);
    draw_track(nv.data(), 16, 8, t, 100);
    for (int y = 0; y < 8; ++y)
        for (int x = 0; x < 16; ++x)
            CHECK(nv[y * 16 + x] == ((x >= 4 && x < 8 && y >= 2 && y < 4) ? 16 : 128));
}

NXTEST(transparent_parchment_restores_background_and_tints_only_the_mask) {
    Track t{2, 1, true, {{0, 2, 2, 2, 2}}, {0, 0, 0, 0, 0, 0, 0, 255}};
    std::vector<uint8_t> nv(8 * 8 * 3 / 2, 128);
    std::fill_n(nv.begin(), 8 * 8, uint8_t(112));
    for (size_t i = 8 * 8; i < nv.size(); i += 2) {
        nv[i] = 60;
        nv[i + 1] = 160;
    }
    nv[2 * 8 + 2] = nv[3 * 8 + 2] = 0;
    nv[64 + 8 + 2] = nv[64 + 8 + 3] = 128; // desaturated source text contaminated a chroma cell
    const auto original = nv;
    draw_track(nv.data(), 8, 8, t, 0);
    CHECK(nv[2 * 8 + 2] == 112 && nv[3 * 8 + 2] == 112);
    CHECK(nv[2 * 8 + 3] == 16 && nv[3 * 8 + 3] == 16);
    CHECK(nv[64 + 8 + 2] == 60 && nv[64 + 8 + 3] == 160);
    CHECK(nv[2 * 8 + 4] == original[2 * 8 + 4]);
}

NXTEST(odd_clipped_area_preserves_shared_chroma_and_outside_guards) {
    Track t{1, 1, false, {{5, -1, -1, 2, 2}}, {255, 0, 0, 255}};
    std::vector<uint8_t> buf(8 * 8 * 3 / 2 + 2, 128);
    draw_track(buf.data() + 1, 8, 8, t, 5);
    CHECK(buf.front() == 128 && buf.back() == 128);
    CHECK(buf[1] == 63 && buf[2] == 128);
    CHECK(buf[1 + 64] == 122 && buf[1 + 65] == 156);
}
