// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <emmintrin.h>
#include <vector>

namespace nxrt::games::fefw::movie_labels {

struct Key {
    int ms, x, y, w, h;
};
struct Track {
    int w, h;
    bool paper;
    std::vector<Key> keys;
    std::vector<uint8_t> rgba;
    int constant_ink = -1;
};
struct Film {
    int w, h;
    std::vector<Track> tracks;
};

inline int mix(int a, int b, int alpha) { return (a * (255 - alpha) + b * alpha + 127) / 255; }

// Grayscale closing fills narrow dark strokes inside the annotation while
// keeping bright map details and slowly varying parchment intact. The extra
// border lets the filter inspect the true paper beyond a clipped glyph box.
inline std::vector<uint8_t> paper_luma(const uint8_t* y, int stride, int rows, int x0, int y0, int x1, int y1, int radius) {
    const int left = std::max(0, x0 - 2 * radius), right = std::min(stride, x1 + 2 * radius);
    const int top = std::max(0, y0 - 2 * radius), bottom = std::min(rows, y1 + 2 * radius);
    const int w = right - left, h = bottom - top;
    std::vector<uint8_t> a(size_t(w) * h), b(a.size());
    for (int r = 0; r < h; ++r)
        std::copy_n(y + (r + top) * stride + left, w, a.data() + r * w);
    auto pass = [&]<bool horizontal, bool maximum>() {
        std::vector<uint8_t> padded(w + 2 * radius);
        for (int row = 0; row < h; ++row) {
            const uint8_t* taps[31];
            if constexpr (horizontal) {
                const auto* source = a.data() + row * w;
                std::fill_n(padded.data(), radius, source[0]);
                std::copy_n(source, w, padded.data() + radius);
                std::fill_n(padded.data() + radius + w, radius, source[w - 1]);
                for (int k = 0; k < 2 * radius + 1; ++k)
                    taps[k] = padded.data() + k;
            } else {
                for (int k = 0; k < 2 * radius + 1; ++k)
                    taps[k] = a.data() + std::clamp(row + k - radius, 0, h - 1) * w;
            }
            int col = 0;
            while (w >= 16 && col < w) {
                // Overlap the previous block at the right border, so even tiny
                // labels avoid scalar boundary loops for a 31-pixel filter.
                const int x = std::min(col, w - 16);
                __m128i best = _mm_loadu_si128(reinterpret_cast<const __m128i*>(taps[0] + x));
                for (int k = 1; k < 2 * radius + 1; ++k) {
                    const auto v = _mm_loadu_si128(reinterpret_cast<const __m128i*>(taps[k] + x));
                    best = maximum ? _mm_max_epu8(best, v) : _mm_min_epu8(best, v);
                }
                _mm_storeu_si128(reinterpret_cast<__m128i*>(b.data() + row * w + x), best);
                col += 16;
            }
            for (; col < w; ++col) {
                uint8_t value = taps[0][col];
                for (int k = 1; k < 2 * radius + 1; ++k)
                    value = maximum ? std::max(value, taps[k][col]) : std::min(value, taps[k][col]);
                b[row * w + col] = value;
            }
        }
        a.swap(b);
    };
    pass.template operator()<true, true>();
    pass.template operator()<false, true>();
    pass.template operator()<true, false>();
    pass.template operator()<false, false>();
    std::vector<uint8_t> out(size_t(x1 - x0) * (y1 - y0));
    for (int row = y0; row < y1; ++row)
        for (int col = x0; col < x1; ++col) {
            const int i = row * stride + col, original = y[i];
            const int filled = a[(row - top) * w + col - left], delta = filled - original;
            int alpha = std::clamp((delta - 6) * 20, 0, 255);
            // Long, continuous map lines often cross the label box. A printed
            // Latin stroke is shorter than all these probes on one axis.
            const int len = std::max(6, radius * 4);
            auto rule = [&](int dx, int dy) {
                if (col - len * dx < 0 || col + len * dx >= stride || row - len * dy < 0 || row + len * dy >= rows) return false;
                for (int distance : {-len, -len / 2, len / 2, len})
                    if (y[i + distance * (dx + dy * stride)] > filled - delta / 2) return false;
                return true;
            };
            if (alpha && (rule(1, 0) || rule(0, 1))) alpha = 0;
            out[(row - y0) * (x1 - x0) + col - x0] = uint8_t(mix(original, filled, alpha));
        }
    return out;
}

// Dark printed ink also desaturates the 4:2:0 chroma sample around the ink.
// Recover that chroma from nearby paper with similar *original* luma. The
// matte is restricted to cells where old ink was actually lifted by closing.
inline void restore_paper_chroma(uint8_t* nv, int w, int h, int x0, int y0, int x1, int y1, std::vector<uint8_t>& paper) {
    auto* uv = nv + size_t(w) * h;
    constexpr int dx[] = {-1, 1, 0, 0, -1, 1, -1, 1}, dy[] = {0, 0, -1, 1, -1, -1, 1, 1};
    const int cw = w / 2, ch = h / 2;
    for (int cy = y0 / 2; cy < (y1 + 1) / 2; ++cy)
        for (int cx = x0 / 2; cx < (x1 + 1) / 2; ++cx) {
            int lifted = 0, target = 0;
            for (int y = 2 * cy; y < 2 * cy + 2; ++y)
                for (int x = 2 * cx; x < 2 * cx + 2; ++x) {
                    const int source = nv[y * w + x];
                    const int value = (x >= x0 && x < x1 && y >= y0 && y < y1) ? paper[(y - y0) * (x1 - x0) + x - x0] : source;
                    lifted += value - source;
                    target += value;
                }
            if (lifted <= 8) continue;
            target = (target + 2) / 4;
            int u = 0, v = 0, donor_y = 0, total = 0;
            for (int r = 2; r <= 14 && total < 420; ++r) {
                for (int k = 0; k < 8; ++k) {
                    const int x = cx + r * dx[k], y = cy + r * dy[k];
                    if (x < 0 || x >= cw || y < 0 || y >= ch) continue;
                    const int p = 2 * y * w + 2 * x;
                    const int luma = (nv[p] + nv[p + 1] + nv[p + w] + nv[p + w + 1] + 2) / 4;
                    if (std::min({nv[p], nv[p + 1], nv[p + w], nv[p + w + 1]}) < target - 18) continue;
                    const int tolerance = std::max(3, 15 - std::abs(luma - target));
                    if (std::abs(luma - target) > 18) continue;
                    const int weight = (15 - r) * tolerance;
                    u += uv[2 * x + y * w] * weight;
                    v += uv[2 * x + y * w + 1] * weight;
                    donor_y += luma * weight;
                    total += weight;
                }
            }
            if (total) {
                const int alpha = std::min(255, (lifted - 8) * 7), pos = 2 * cx + cy * w;
                uv[pos] = uint8_t(mix(uv[pos], (u + total / 2) / total, alpha));
                uv[pos + 1] = uint8_t(mix(uv[pos + 1], (v + total / 2) / total, alpha));
                const int donor = (donor_y + total / 2) / total;
                for (int y = std::max(2 * cy, y0); y < std::min(2 * cy + 2, y1); ++y)
                    for (int x = std::max(2 * cx, x0); x < std::min(2 * cx + 2, x1); ++x) {
                        auto& yout = paper[(y - y0) * (x1 - x0) + x - x0];
                        if (yout - nv[y * w + x] > 12) yout = uint8_t((2 * yout + donor + 1) / 3);
                    }
            }
        }
}

inline void draw_track(uint8_t* nv, int width, int height, const Track& track, int ms) {
    if (ms < track.keys.front().ms || ms > track.keys.back().ms) return;
    auto end = std::lower_bound(track.keys.begin(), track.keys.end(), ms, [](const Key& k, int at) { return k.ms < at; });
    Key box = *end;
    if (end != track.keys.begin() && box.ms != ms) {
        const auto& a = *(end - 1);
        const double t = double(ms - a.ms) / (box.ms - a.ms);
        auto at = [t](int from, int to) { return int(std::lround(from + t * (to - from))); };
        box.x = at(a.x, box.x);
        box.y = at(a.y, box.y);
        box.w = at(a.w, box.w);
        box.h = at(a.h, box.h);
    }
    const int x0 = std::max(0, box.x), x1 = std::min(width, box.x + box.w);
    const int y0 = std::max(0, box.y), y1 = std::min(height, box.y + box.h);
    if (x1 <= x0 || y1 <= y0) return;
    auto color = [&](int x, int y) {
        // Bilinear resampling at output pixel centers keeps small Arabic strokes smooth across camera zooms.
        const double u = std::clamp((x - box.x + 0.5) * track.w / box.w - 0.5, 0.0, double(track.w - 1));
        const double v = std::clamp((y - box.y + 0.5) * track.h / box.h - 0.5, 0.0, double(track.h - 1));
        const int i = int(u), j = int(v), ax = int((u - i) * 255 + .5), ay = int((v - j) * 255 + .5);
        const auto* a = &track.rgba[(j * track.w + i) * 4];
        const auto* b = a + (i + 1 < track.w ? 4 : 0);
        const int down = j + 1 < track.h ? track.w * 4 : 0;
        std::array<int, 4> c{};
        for (size_t k = 0; k < c.size(); ++k)
            c[k] = mix(mix(a[k], b[k], ax), mix(a[down + k], b[down + k], ax), ay);
        return c;
    };
    std::vector<uint8_t> paper;
    if (track.paper) {
        paper = paper_luma(nv, width, height, x0, y0, x1, y1, std::clamp(box.h / 7, 1, 15));
        restore_paper_chroma(nv, width, height, x0, y0, x1, y1, paper);
        if (track.constant_ink >= 0) {
            struct Axis {
                int a, b, frac;
            };
            const auto axis = [](int pos, int from, int size, int samples) {
                const double coordinate = std::clamp((pos - from + 0.5) * samples / size - 0.5, 0., double(samples - 1));
                const int a = int(coordinate);
                return Axis{a, std::min(a + 1, samples - 1), int((coordinate - a) * 255 + .5)};
            };
            std::vector<Axis> columns;
            columns.reserve(x1 - x0);
            for (int x = x0; x < x1; ++x)
                columns.push_back(axis(x, box.x, box.w, track.w));
            for (int y = y0; y < y1; ++y) {
                const auto v = axis(y, box.y, box.h, track.h);
                const auto* first = track.rgba.data() + v.a * track.w * 4 + 3;
                const auto* second = track.rgba.data() + v.b * track.w * 4 + 3;
                for (int x = x0; x < x1; ++x) {
                    const auto& u = columns[x - x0];
                    const int alpha =
                        mix(mix(first[u.a * 4], first[u.b * 4], u.frac), mix(second[u.a * 4], second[u.b * 4], u.frac), v.frac);
                    const int bg = paper[(y - y0) * (x1 - x0) + x - x0];
                    const int ink = 16 + (track.constant_ink - 16) * std::clamp(bg - 16, 0, 219) / 219;
                    nv[y * width + x] = uint8_t(mix(bg, ink, alpha));
                }
            }
            return;
        }
    }
    const int cy0 = y0 / 2, cy1 = (y1 + 1) / 2;
    uint8_t* uv = nv + size_t(width) * height;
    // The clamp here also handles the tiny rounding errors of the BT.709 limited-range conversion.
    auto channel = [](int c) { return static_cast<uint8_t>(std::clamp(c, 0, 255)); };
    for (int cy = cy0; cy < cy1; ++cy) {
        for (int cx = x0 / 2; cx < (x1 + 1) / 2; ++cx) {
            int sum_u = 0, sum_v = 0;
            auto* chroma = uv + cy * width + cx * 2;
            const int bg_u = chroma[0], bg_v = chroma[1];
            for (int y = cy * 2; y < cy * 2 + 2; ++y) {
                for (int x = cx * 2; x < cx * 2 + 2; ++x) {
                    if (x < x0 || x >= x1 || y < y0 || y >= y1) {
                        sum_u += chroma[0];
                        sum_v += chroma[1];
                        continue;
                    }
                    const auto c = color(x, y);
                    int ink_y = 16 + ((47 * c[0] + 157 * c[1] + 16 * c[2] + 128) >> 8);
                    int ink_u = 128 + ((-26 * c[0] - 87 * c[1] + 113 * c[2] + 128) >> 8);
                    int ink_v = 128 + ((113 * c[0] - 103 * c[1] - 10 * c[2] + 128) >> 8);
                    int bg_y = nv[y * width + x];
                    if (track.paper) {
                        bg_y = paper[(y - y0) * (x1 - x0) + x - x0];
                        const int light = std::clamp(bg_y - 16, 0, 219);
                        ink_y = 16 + (ink_y - 16) * light / 219;
                        ink_u = bg_u;
                        ink_v = bg_v;
                    }
                    nv[y * width + x] = channel(mix(bg_y, ink_y, c[3]));
                    sum_u += mix(bg_u, ink_u, c[3]);
                    sum_v += mix(bg_v, ink_v, c[3]);
                }
            }
            chroma[0] = channel((sum_u + 2) / 4);
            chroma[1] = channel((sum_v + 2) / 4);
        }
    }
}
} // namespace nxrt::games::fefw::movie_labels
