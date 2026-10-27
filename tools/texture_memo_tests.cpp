// Unit test for runtime/native_texture_memo.h (NATIVE_TEXTURE_VALIDATION_MEMO_V1, per-page epochs). No game, no GL.
// A model of the write tracker stands in for native_guest_arena.cpp: a page is armed when a clean-check query covers it; a
// write to an armed page disarms it and stamps the page with a fresh epoch (as tracker_fault and HostWriteScope do); a write
// to an unarmed page changes nothing. native_ram_pages_epoch() is the largest page epoch over the pages of the range.
// The memo must be invalidated by a write to any page of its own entry and by a TLUT change, must survive writes to other
// pages, and must never arm for an entry the tracker did not answer clean for.
//
// Build & run: see tools/check_texture_validation_memo.py (it provides runtime/native_tlut_epoch.h on the include path).
#include <cstdint>
#include <cstdio>
#include <set>
#include "runtime/native_texture_memo.h"

namespace nwii::runtime {
namespace {
constexpr int kPages = 64;
uint64_t g_model_epoch = 1;
uint64_t g_page_epoch[kPages];
bool g_armed[kPages];
}
// The model ignores the bank pointer: `offset` selects the page (offset / 4096), as the arena does for the tracked range.
uint64_t native_ram_pages_epoch(const uint8_t*, size_t, size_t offset, size_t bytes) {
    if (!bytes) return UINT64_MAX;
    uint64_t newest = 0;
    for (size_t p = offset / 4096; p <= (offset + bytes - 1) / 4096; ++p) {
        if (g_page_epoch[p] > newest) newest = g_page_epoch[p];
    }
    return newest;
}
}  // namespace nwii::runtime

namespace {

int failures = 0, checks = 0;

void expect(bool ok, const char* what) {
    ++checks;
    if (!ok) { ++failures; std::printf("FAIL %s\n", what); }
}

// A write by the guest or the host to page `page`.
void write_page(int page) {
    if (nwii::runtime::g_armed[page]) {
        nwii::runtime::g_armed[page] = false;
        nwii::runtime::g_page_epoch[page] = ++nwii::runtime::g_model_epoch;
    }
}

// The entry occupies pages [first, first + count). The tracker's clean answer arms those pages and records the memo with the
// page epoch and TLUT epoch sampled before the query, as texture_cache_gl.cpp does. An untracked answer never arms.
void verify_clean(native_texture_memo::Memo& memo, int first, int count, bool tracked) {
    const size_t offset = size_t(first) * 4096, bytes = size_t(count) * 4096;
    const uint64_t epoch_before = nwii::runtime::native_ram_pages_epoch(nullptr, 0, offset, bytes);
    const uint64_t tlut_before = native_tlut::Read();
    if (!tracked) return;
    for (int p = first; p < first + count; ++p) nwii::runtime::g_armed[p] = true;
    native_texture_memo::Arm(memo, epoch_before, tlut_before);
}

bool valid(const native_texture_memo::Memo& memo, int first, int count) {
    const size_t offset = size_t(first) * 4096, bytes = size_t(count) * 4096;
    return native_texture_memo::Valid(memo, nwii::runtime::native_ram_pages_epoch(nullptr, 0, offset, bytes), native_tlut::Read());
}

}  // namespace

int main() {
    for (int p = 0; p < 64; ++p) { nwii::runtime::g_armed[p] = false; nwii::runtime::g_page_epoch[p] = 0; }

    native_texture_memo::Memo memo;
    expect(!valid(memo, 3, 2), "a new entry has no memo");

    verify_clean(memo, 3, 2, true);                       // pages 3 and 4
    expect(valid(memo, 3, 2), "memo valid after a clean verification");

    write_page(4);
    expect(!valid(memo, 3, 2), "a write to the entry's second page invalidates the memo");

    verify_clean(memo, 3, 2, true);
    expect(valid(memo, 3, 2), "re-verification re-arms the memo");

    write_page(9);                                        // not armed, not part of the entry
    expect(valid(memo, 3, 2), "a write outside the entry keeps the memo");

    native_texture_memo::Memo other;                      // another entry arms page 20
    verify_clean(other, 20, 1, true);
    write_page(20);
    expect(!valid(other, 20, 1), "the other entry's memo is invalidated by its own page");
    expect(valid(memo, 3, 2), "a write to another entry's armed page keeps this memo");

    write_page(3);                                        // the entry's first page
    expect(!valid(memo, 3, 2), "a write to the entry's first page invalidates the memo");

    verify_clean(memo, 3, 2, true);
    native_tlut::Changed();
    expect(!valid(memo, 3, 2), "a TLUT change invalidates the memo");

    verify_clean(memo, 3, 2, true);
    expect(valid(memo, 3, 2), "re-verification after a TLUT change re-arms the memo");

    // Pages that are not tracked (the arena answers UINT64_MAX for them) never validate.
    native_texture_memo::Memo untracked;
    verify_clean(untracked, 7, 1, false);
    expect(!valid(untracked, 7, 1), "an untracked entry never arms");
    native_texture_memo::Memo unknown;
    native_texture_memo::Arm(unknown, UINT64_MAX, 0);
    expect(!unknown.armed && !valid(unknown, 7, 1), "an unknown epoch never arms");

    native_texture_memo::Disarm(memo);
    expect(!valid(memo, 3, 2), "Disarm drops the memo");

    // NATIVE_TEXTURE_TRACK_THRASH_V1: the bypass is a cost rule, never a correctness one - it only stops asking the tracker.
    native_texture_memo::Memo thrash;
    expect(!native_texture_memo::SkipTrackerQuery(thrash, 4096), "a new entry asks the tracker");
    for (int i = 0; i + 1 < native_texture_memo::kThrashHits; ++i) native_texture_memo::NoteDirtyQuery(thrash);
    expect(!native_texture_memo::SkipTrackerQuery(thrash, 4096), "fewer than kThrashHits dirty answers still ask the tracker");
    native_texture_memo::NoteDirtyQuery(thrash);
    int skipped = 0;
    while (native_texture_memo::SkipTrackerQuery(thrash, 4096)) ++skipped;
    expect(skipped == native_texture_memo::kProbeQueries, "kThrashHits dirty answers skip kProbeQueries validations");
    expect(native_texture_memo::SkipTrackerQuery(thrash, native_texture_memo::kSkipMaxBytes + 1) == false,
           "a range above kSkipMaxBytes always asks the tracker");
    native_texture_memo::NoteDirtyQuery(thrash);                       // the probe answered dirty: skip again
    expect(native_texture_memo::SkipTrackerQuery(thrash, 4096), "a dirty probe resumes skipping");
    int resumed = 1;
    while (native_texture_memo::SkipTrackerQuery(thrash, 4096)) ++resumed;
    expect(resumed == native_texture_memo::kProbeQueries, "the resumed skip spends the same probe budget");
    native_texture_memo::Arm(thrash, 11, 0);                           // a clean answer ends the bypass
    expect(!native_texture_memo::SkipTrackerQuery(thrash, 4096), "a clean answer stops skipping");

    std::printf("texture memo: %d checks, failures %d\n", checks, failures);
    std::printf("%s\n", failures ? "TEXTURE_MEMO_TESTS FAIL" : "TEXTURE_MEMO_TESTS PASS");
    return failures ? 1 : 0;
}
