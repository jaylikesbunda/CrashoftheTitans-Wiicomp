#pragma once
// NATIVE_TEXTURE_VALIDATION_MEMO_V1 (per-page epochs): a texture entry that was verified clean by the write tracker is not
// re-validated while no page of ITS OWN range has been written and the TLUT is unchanged.
//
// Soundness: Arm() is called only when the tracker answered clean for the entry's pages (every page armed and not dirty), with
// the page epoch of the range sampled BEFORE that query (native_ram_pages_epoch). Armed pages trap the next guest or host write,
// and tracker_fault and HostWriteScope stamp the written page with a fresh epoch from one global counter. Epochs only grow, so
// the maximum over a fixed set of pages changes whenever any page of the set is stamped (the fresh value exceeds every value
// handed out before). A TLUT change advances native_tlut::Read(). Any change disarms the memo by mismatch; the entry then takes
// the ordinary validation. Writes to other pages leave the epoch of this range alone.
//
// Untracked, sticky and mask/GetWriteWatch pages answer UINT64_MAX from native_ram_pages_epoch, so they never arm.
// Opt-out: NWII_NO_TEXTURE_VALIDATION_MEMO=1.
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include "runtime/native_tlut_epoch.h"

namespace nwii::runtime {
// Largest write epoch among the tracked pages of [offset, offset+bytes) of the bank at `base` (the arena tracker's page epochs).
// UINT64_MAX when the range is not tracked by the arena tracker (untracked, sticky, mask layout, tracker off).
uint64_t native_ram_pages_epoch(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes);
// Pages armed by the tracker over the whole run (texture.track_query attribution: pages armed per query = delta of this).
uint64_t native_ram_pages_armed_total();
}

namespace native_texture_memo {

struct Memo {
    uint64_t epoch = 0;
    uint64_t tlut = 0;
    bool armed = false;
    // NATIVE_TEXTURE_TRACK_THRASH_V1: an entry whose pages the guest rewrites every frame never arms (its range answers dirty
    // on every query), so each validation pays a query that cannot prove anything and then compares the bytes anyway.
    // Those answers are counted; after kThrashHits in a row the entry stops querying for kProbeQueries validations, then lets
    // one query through to see whether the pages settled. Only the query is skipped - the bytes are still compared - so the
    // result stays exact and this is purely a cost rule. Large ranges keep querying: a comparison costs about as much as one
    // query at kSkipMaxBytes (measured in native_texture_hash_cache.h), and above that the query is the cheaper side.
    uint8_t dirty_hits = 0;
    uint8_t probe = 0;
    bool skip_query = false;
};

// Ranges above this keep asking the tracker: memcmp at 64 KiB costs about one tracked query.
constexpr size_t kSkipMaxBytes = 65536;
constexpr uint8_t kThrashHits = 8;
constexpr uint8_t kProbeQueries = 64;

inline bool Enabled() {
    static const bool on = std::getenv("NWII_NO_TEXTURE_VALIDATION_MEMO") == nullptr;
    return on;
}
// `epoch_before_query` is native_ram_pages_epoch() sampled before the clean query; UINT64_MAX (untracked) never arms.
inline void Arm(Memo& m, uint64_t epoch_before_query, uint64_t tlut_before_query) {
    m.armed = epoch_before_query != UINT64_MAX;
    m.epoch = epoch_before_query;
    m.tlut = tlut_before_query;
    // A clean answer means the pages settled: stop skipping queries for this entry.
    m.dirty_hits = 0;
    m.probe = 0;
    m.skip_query = false;
}
inline void Disarm(Memo& m) { m.armed = false; }

// True when this validation should go straight to the byte comparison instead of asking the tracker. Also spends one probe.
inline bool SkipTrackerQuery(Memo& m, size_t bytes) {
    if (!m.skip_query || bytes > kSkipMaxBytes) return false;
    if (m.probe) { --m.probe; return true; }
    m.skip_query = false;   // the probe query runs now; NoteDirtyQuery() resumes skipping if it answered dirty again
    return false;
}
// Called after a query answered dirty and the bytes still matched (so the entry was re-verified, not replaced).
inline void NoteDirtyQuery(Memo& m) {
    if (m.dirty_hits < kThrashHits) ++m.dirty_hits;
    if (m.dirty_hits >= kThrashHits) { m.skip_query = true; m.probe = kProbeQueries; }
}
// `epoch_now` is native_ram_pages_epoch() of the entry's range now.
inline bool Valid(const Memo& m, uint64_t epoch_now, uint64_t tlut_now) {
    return m.armed && epoch_now != UINT64_MAX && m.epoch == epoch_now && m.tlut == tlut_now;
}

}  // namespace native_texture_memo
