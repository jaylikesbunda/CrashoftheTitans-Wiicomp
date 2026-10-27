#pragma once
// NATIVE_TEXTURE_HASH_CACHE_V1: the decisions behind the texture cache's exact validation. Dependency-free (no GL, no MMU)
// so the rules can be unit tested: tools/texture_hash_cache_tests.cpp. The stage that uses this is
// tools/fix_texture_hash_cache.py.
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace native_texture_hash {

// NWII_NO_TEXTURE_HASH_CACHE=1 turns both shortcuts off: every lookup hashes its source and palette again, and every
// validation is a byte comparison (the staged behaviour before this stage).
inline bool Enabled() {
    static const bool on = std::getenv("NWII_NO_TEXTURE_HASH_CACHE") == nullptr;
    return on;
}

// Parses NWII_TEXTURE_TRACK_MIN (bytes). Unset, empty, malformed or outside [4 KiB, 16 MiB] gives the default.
inline uint32_t ParseTrackMin(const char* text) {
    constexpr uint32_t kDefault = 4096, kLow = 4096, kHigh = 1u << 24;
    if (!text || !*text) return kDefault;
    char* end = nullptr;
    const unsigned long value = std::strtoul(text, &end, 10);
    if (end == text || *end != '\0' || value < kLow || value > kHigh) return kDefault;
    return uint32_t(value);
}

// Smallest source size whose validation asks the write tracker. Smaller sources are compared directly. Measured with
// tools/guest_arena_tests/arena_misc.cpp (NWII_TRACK_BENCH=1, software tracker, warm cache, per call):
//   bytes   1024   2048   4096   8192   16384  32768  65536
//   query     21-54   31    22-32  23-39  29-34  41-43  67-77 ns   (flat: one lock and one page-table walk per page)
//   memcmp   41     70-80  147-183 289-348 610-633 1164-1232 2342-2412 ns
// The crossover is near 1 KiB warm; 4 KiB keeps a 5x margin for cold reads of guest RAM. Entries under the threshold are
// compared every time, so the default is the smallest value the parser accepts.
inline uint32_t TrackMinBytes() {
    static const uint32_t value = ParseTrackMin(std::getenv("NWII_TEXTURE_TRACK_MIN"));
    return value;
}

// The only way a cached texture is accepted without comparing its bytes. True when the tracker answered for the entry's pages
// (`newest` is native_ram_newest_write(): UINT64_MAX means untracked), the entry was verified (`verified_gen` is the generation
// sampled BEFORE its bytes were last compared, 0 means never) and no page of the source was stamped after that generation.
inline bool CleanSince(uint64_t newest, uint64_t verified_gen) {
    return newest != UINT64_MAX && verified_gen != 0 && newest <= verified_gen;
}

}  // namespace native_texture_hash
