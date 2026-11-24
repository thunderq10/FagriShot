// SPDX-License-Identifier: GPL-3.0-or-later
#include "fefw_arabic_movies.h"
#include "nxrt/log.h"
#include "nxrt/movie_vdec.h"
#include <windows.h>
#include <bcrypt.h>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <span>
#include <stdexcept>
#include <unordered_map>
#include <zstd.h>
#pragma comment(lib, "bcrypt")

namespace nxrt::games::fefw {
namespace {
using namespace movie_labels;
struct Sample {
    uint32_t size;
    uint64_t hint;
    std::array<uint8_t, 16> digest;
    uint32_t film, ms;
};
struct Recipe {
    std::vector<Film> films;
    std::vector<Sample> samples;
    std::unordered_multimap<uint32_t, size_t> anchors;
};
Recipe recipe;

struct Reader {
    std::span<const uint8_t> rest;
    std::span<const uint8_t> take(size_t n) {
        if (n > rest.size()) throw std::runtime_error("truncated movie label data");
        const auto data = rest.first(n);
        rest = rest.subspan(n);
        return data;
    }
    template <class T> T read() {
        T t;
        std::memcpy(&t, take(sizeof(t)).data(), sizeof(t));
        return t; // Windows x64 little endian
    }
};

Recipe read_recipe(std::istream& stream, size_t bytes) {
    if (bytes < 16 || bytes > 64 * 1024 * 1024) throw std::runtime_error("invalid movie label data size");
    std::vector<uint8_t> storage(bytes);
    if (!stream.read(reinterpret_cast<char*>(storage.data()), bytes)) throw std::runtime_error("cannot read movie labels");
    Reader data{storage};
    if (std::memcmp(data.take(8).data(), "FAGMAP01", 8)) throw std::runtime_error("unsupported movie label data");
    auto films = data.read<uint32_t>(), samples = data.read<uint32_t>();
    if (!films || films > 5 || !samples || samples > 100000) throw std::runtime_error("invalid movie label counts");
    Recipe out;
    size_t expanded = 0;
    for (unsigned f = 0; f < films; ++f) {
        Film film{data.read<uint16_t>(), data.read<uint16_t>(), {}};
        auto tracks = data.read<uint32_t>();
        if (film.w != 2560 || film.h != 1440 || !tracks || tracks > 512) throw std::runtime_error("invalid film");
        for (unsigned t = 0; t < tracks; ++t) {
            Track track{};
            track.w = data.read<uint16_t>();
            track.h = data.read<uint16_t>();
            const auto flags = data.read<uint8_t>();
            track.paper = flags == 1;
            const auto keys = data.read<uint32_t>();
            if (flags > 1 || !track.w || track.w > 2048 || !track.h || track.h > 512 || !keys || keys > 20000)
                throw std::runtime_error("invalid movie track");
            for (unsigned k = 0; k < keys; ++k) {
                Key key{static_cast<int>(data.read<uint32_t>()), data.read<int16_t>(), data.read<int16_t>(), data.read<int16_t>(),
                        data.read<int16_t>()};
                if (key.ms < 0 || key.ms >= 3600000 || (!track.keys.empty() && key.ms <= track.keys.back().ms) ||
                    std::abs(key.x) > 8192 || std::abs(key.y) > 8192 || key.w < 1 || key.w > 8192 || key.h < 1 || key.h > 8192)
                    throw std::runtime_error("invalid movie keyframe");
                track.keys.push_back(key);
            }
            const size_t expected = size_t(track.w) * track.h * 4;
            expanded += expected;
            const auto pixels = data.take(data.read<uint32_t>());
            if (expanded > 128 * 1024 * 1024 || ZSTD_getFrameContentSize(pixels.data(), pixels.size()) != expected)
                throw std::runtime_error("invalid compressed movie mask size");
            track.rgba.resize(expected);
            if (ZSTD_decompress(track.rgba.data(), expected, pixels.data(), pixels.size()) != expected)
                throw std::runtime_error("invalid compressed movie mask");
            if (track.paper) {
                const auto* p = track.rgba.data();
                bool constant = true;
                for (size_t j = 4; j < expected; j += 4) {
                    if (std::memcmp(p, p + j, 3)) {
                        constant = false;
                        break;
                    }
                }
                if (constant) track.constant_ink = 16 + ((47 * p[0] + 157 * p[1] + 16 * p[2] + 128) >> 8);
            }
            film.tracks.push_back(std::move(track));
        }
        out.films.push_back(std::move(film));
    }
    out.samples.reserve(samples);
    for (unsigned i = 0; i < samples; ++i) {
        Sample s{};
        s.size = data.read<uint32_t>();
        s.hint = data.read<uint64_t>();
        s.digest = data.read<decltype(s.digest)>();
        s.film = data.read<uint16_t>();
        s.ms = data.read<uint32_t>();
        const unsigned unique = data.read<uint8_t>();
        if (!s.size || s.film >= films || s.ms >= 3600000 || unique > 1) throw std::runtime_error("invalid movie sample");
        out.samples.push_back(s);
        if (unique) out.anchors.emplace(s.size, i);
    }
    if (!data.rest.empty()) throw std::runtime_error("unexpected trailing movie data");
    return out;
}

int64_t identify(const uint8_t* bytes, size_t length, int64_t prior) {
    if (!length || length > UINT32_MAX) return 0;
    const bool has_next = prior > 0 && uint64_t(prior) < recipe.samples.size() &&
                          recipe.samples[prior - 1].film == recipe.samples[prior].film && recipe.samples[prior].size == length;
    const auto range = recipe.anchors.equal_range(static_cast<uint32_t>(length));
    if (!has_next && range.first == range.second) return 0;
    uint64_t hint = 14695981039346656037ull;
    for (size_t i = 0; i < std::min<size_t>(length, 64); ++i)
        hint = (hint ^ bytes[i]) * 1099511628211ull;
    std::array<uint8_t, 32> sha{};
    bool calculated = false;
    auto matches = [&](const Sample& s) {
        if (s.hint != hint) return false;
        if (!calculated) {
            if (BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0, const_cast<PUCHAR>(bytes), static_cast<ULONG>(length), sha.data(),
                           ULONG(sha.size())) < 0)
                return false;
            calculated = true;
        }
        return std::equal(s.digest.begin(), s.digest.end(), sha.begin());
    };
    if (has_next && matches(recipe.samples[prior])) return prior + 1;
    for (auto i = range.first; i != range.second; ++i)
        if (matches(recipe.samples[i->second])) return static_cast<int64_t>(i->second) + 1;
    return 0;
}

void draw(int64_t token, uint8_t* nv, uint32_t w, uint32_t h) {
    if (!nv || token <= 0 || uint64_t(token) > recipe.samples.size()) return;
    const auto& s = recipe.samples[token - 1];
    const auto& film = recipe.films[s.film];
    if (w != unsigned(film.w) || h != unsigned(film.h)) return;
    for (const auto& t : film.tracks)
        draw_track(nv, film.w, film.h, t, s.ms);
}
} // namespace

void install_movie_labels() {
    // Game registration happens before config_init. Use only the two explicit
    // Unicode environment values set by the opted-in player launch.
    wchar_t enabled[4]{};
    if (GetEnvironmentVariableW(L"NXRT_FEFW_ARABIC_REVEAL", enabled, 4) != 1 || enabled[0] != L'1') return;
    std::vector<wchar_t> path(32768);
    const DWORD n = GetEnvironmentVariableW(L"NXRT_FEFW_ARABIC_MOVIE_MAP", path.data(), DWORD(path.size()));
    if (!n || n >= path.size()) return;
    try {
        std::ifstream file(std::filesystem::path(path.data()), std::ios::binary | std::ios::ate);
        if (!file) throw std::runtime_error("cannot open movie label recipe");
        const auto bytes = file.tellg();
        file.seekg(0);
        recipe = read_recipe(file, static_cast<size_t>(bytes));
        static const media::MovieOverlay overlay{identify, draw};
        media::set_movie_overlay(&overlay);
    } catch (const std::exception& e) {
        NXLOG_W("fefw.movie", "Arabic movie labels disabled: %s", e.what());
    }
}
} // namespace nxrt::games::fefw
