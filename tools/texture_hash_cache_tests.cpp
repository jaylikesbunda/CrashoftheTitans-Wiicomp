// NATIVE_TEXTURE_HASH_CACHE_V1 unit test: the validation rules of runtime-overlays/native_texture_hash_cache.h, and a model of
// the write tracker + texture entry lifecycle that checks the safety property on random histories:
//   a cached entry is accepted without a byte comparison only when its bytes are the bytes now in guest memory.
// No GL, no game. Build and run: tools/check_texture_hash_cache.py.
#include "native_texture_hash_cache.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

using native_texture_hash::CleanSince;
using native_texture_hash::ParseTrackMin;

static unsigned failures = 0;
#define CHECK(cond, msg)                                                  \
    do {                                                                  \
        if (!(cond)) { ++failures; std::printf("FAIL %s (line %d)\n", msg, __LINE__); } \
    } while (0)

// ---- model of the tracker (native_ram_newest_write / native_ram_generation) -----------------------------------------
// A page is dirty from the moment of a guest store until the next query, which stamps it with a new generation. A query returns
// the newest stamp over the pages of the range. The generation sampled after a query is the newest stamp issued so far.
struct TrackerModel {
    static constexpr size_t kPages = 64, kBytes = kPages * 4096;
    std::vector<uint8_t> mem = std::vector<uint8_t>(kBytes, 0);
    uint64_t page_gen[kPages] = {};
    bool dirty[kPages] = {};
    uint64_t counter = 1; // g_generation starts at 1

    void store(size_t offset, uint8_t value) {
        mem[offset] = value;
        dirty[offset >> 12] = true;
    }
    uint64_t query(size_t offset, size_t bytes) {
        uint64_t newest = 0;
        for (size_t p = offset >> 12; p <= (offset + bytes - 1) >> 12; ++p) {
            if (dirty[p]) { page_gen[p] = ++counter; dirty[p] = false; }
            newest = std::max(newest, page_gen[p]);
        }
        return newest;
    }
    uint64_t generation() const { return counter; }
};

// A cached texture entry: the snapshot taken when it was created, and the generation it was last verified at.
struct EntryModel {
    size_t offset = 0, bytes = 0;
    std::vector<uint8_t> snapshot;
    uint64_t verified_gen = 0;
};

// The lookup of the texture cache's unchanged() for a tracked source (the decision order of the staged code).
static bool Validate(TrackerModel& t, EntryModel& e) {
    const uint64_t newest = t.query(e.offset, e.bytes);
    if (CleanSince(newest, e.verified_gen)) return true;
    const uint64_t sampled = t.generation();          // sampled before the bytes are read
    if (!std::equal(e.snapshot.begin(), e.snapshot.end(), t.mem.begin() + e.offset)) return false;
    e.verified_gen = sampled;
    return true;
}

// The miss path: the generation is sampled after the query and before the bytes are copied.
static void Create(TrackerModel& t, EntryModel& e, size_t offset, size_t bytes) {
    e.offset = offset;
    e.bytes = bytes;
    (void)t.query(offset, bytes);
    e.verified_gen = t.generation();
    e.snapshot.assign(t.mem.begin() + offset, t.mem.begin() + offset + bytes);
}

static void RuleTests() {
    CHECK(!CleanSince(UINT64_MAX, 5), "untracked answer never clean");
    CHECK(!CleanSince(3, 0), "never-verified entry never clean");
    CHECK(!CleanSince(UINT64_MAX, 0), "untracked and never verified never clean");
    CHECK(CleanSince(4, 5), "older stamp is clean");
    CHECK(CleanSince(5, 5), "stamp equal to the verification generation is clean (write was seen before the sample)");
    CHECK(!CleanSince(6, 5), "newer stamp is dirty");

    CHECK(ParseTrackMin(nullptr) == 4096, "unset gives the default");
    CHECK(ParseTrackMin("") == 4096, "empty gives the default");
    CHECK(ParseTrackMin("abc") == 4096, "malformed gives the default");
    CHECK(ParseTrackMin("12x") == 4096, "trailing garbage gives the default");
    CHECK(ParseTrackMin("100") == 4096, "below the floor gives the default");
    CHECK(ParseTrackMin("999999999") == 4096, "above the ceiling gives the default");
    CHECK(ParseTrackMin("16384") == 16384, "valid value is used");
    CHECK(ParseTrackMin("4096") == 4096, "floor is inclusive");
    CHECK(ParseTrackMin("65536") == 65536, "the former default is still accepted");
}

// The safety property on random histories with interleaved stores, lookups of several ranges and re-verifications.
static void ModelTests(unsigned seed, unsigned rounds) {
    std::mt19937 rng(seed);
    TrackerModel t;
    std::vector<EntryModel> entries;
    for (unsigned r = 0; r < rounds; ++r) {
        const unsigned op = unsigned(rng() % 10);
        if (op < 4) {                                     // guest store anywhere
            const size_t offset = rng() % TrackerModel::kBytes;
            t.store(offset, uint8_t(rng()));
        } else if (op < 6 || entries.empty()) {           // create an entry over a random range
            const size_t bytes = 1 + rng() % (3 * 4096);
            const size_t offset = rng() % (TrackerModel::kBytes - bytes);
            entries.emplace_back();
            Create(t, entries.back(), offset, bytes);
        } else {                                          // look an entry up
            EntryModel& e = entries[rng() % entries.size()];
            const bool accepted = Validate(t, e);
            const bool same = std::equal(e.snapshot.begin(), e.snapshot.end(), t.mem.begin() + e.offset);
            if (accepted) CHECK(same, "accepted entry must match current memory");
            if (accepted && !same) return;
            if (!accepted) {
                // A rejected entry is refreshed from memory, as the miss path does (the decode reads the bytes now).
                Create(t, e, e.offset, e.bytes);
            }
        }
    }
}

// Writes after verification must always be seen by the next lookup of that entry (no lost write).
static void WriteAfterVerifyTests() {
    TrackerModel t;
    EntryModel e;
    Create(t, e, 0x2000, 8192);
    CHECK(Validate(t, e), "unchanged entry accepted");
    t.store(0x2000 + 100, 0x7f);                         // one byte inside the range
    CHECK(!CleanSince(t.query(0x2000, 8192), e.verified_gen), "a write inside the range is seen");
    CHECK(!Validate(t, e) || std::equal(e.snapshot.begin(), e.snapshot.end(), t.mem.begin() + e.offset),
          "a write inside the range is never accepted over changed bytes");
    t.store(0x2000 + 8191, 0x11);                        // last byte, last page
    CHECK(!Validate(t, e) || std::equal(e.snapshot.begin(), e.snapshot.end(), t.mem.begin() + e.offset),
          "a write in the last page is never accepted over changed bytes");
    // Writes outside the range do not force a compare-miss for this range: the verdict stays clean.
    Create(t, e, 0x2000, 8192);
    t.store(0x9000, 1);
    CHECK(CleanSince(t.query(0x2000, 8192), e.verified_gen), "a write outside the range leaves the range clean");
}

int main() {
    RuleTests();
    WriteAfterVerifyTests();
    for (unsigned seed = 1; seed <= 200; ++seed) ModelTests(seed, 4000);
    if (failures) {
        std::printf("texture hash cache tests: %u failure(s)\n", failures);
        return 1;
    }
    std::printf("texture hash cache tests: ok (rules, write-after-verify, 200 random histories)\n");
    return 0;
}
