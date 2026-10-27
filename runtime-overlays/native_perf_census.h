#pragma once
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <memory>

// Opt-in work census, independent of the timing overlay. Values are diagnostics,
// never guest state. Each owned lane has one writer; export reads only atomics.
namespace native_perf_census {
inline const bool enabled = std::getenv("NWII_PERF_CENSUS") != nullptr;
// Detailed PC attribution is separate from operation counts. It is opt-in;
// ordinary census runs no longer hash every entry or switch.
inline const bool pc_histograms = enabled && std::getenv("NWII_PERF_PC_HISTOGRAM") != nullptr;
__attribute__((always_inline)) inline bool Enabled() { return enabled; }
enum Counter : unsigned {
  Frames, EntryNormal, EntryResume, EntryContinuation,
  Switch24, Switch64, Switch256, SwitchLarge, DispatchLookup, DispatchMiss,
  PollBranch, PollFull, QuantU8, QuantU16, QuantS8, QuantS16, QuantRam,
  QuantFallback, GatherCalls, GatherBytes, PublishCalls, PublishBytes,
  TextureLookup, TextureMiss, PackedRequests, PackedReuse, PackedAlloc, PackedWords,
  MultipleRequests, MultipleFast, MultipleWords, FillRequests, FillFast,
  XfRequests, XfCached,
  SlowPoll, DelayPoll,
  Format0, FormatEnd = Format0 + 16, Count = FormatEnd
};
inline std::atomic<uint64_t> totals[Count]{};
inline uint64_t guest_values[Count]{}, render_values[Count]{};
inline unsigned guest_pending[Count]{}, render_pending[Count]{};
// Publish once per 128 events of a counter, avoiding shared RMW in hot guest paths.
// Byte/word counters therefore omit the last <=127 operations, not <=127 bytes.
// The guest-side recorders keep only the `enabled` test inline; the work after it is out of line and cold.
__attribute__((noinline, cold)) inline void GuestAddSlow(Counter key, uint64_t amount) {
  guest_values[key] += amount;
  if (++guest_pending[key] == 128) {
    totals[key].store(guest_values[key], std::memory_order_relaxed);
    guest_pending[key] = 0;
  }
}
__attribute__((always_inline)) inline void GuestAdd(Counter key, uint64_t amount=1) {
  if (!enabled) return;
  GuestAddSlow(key, amount);
}
__attribute__((always_inline)) inline void RenderAdd(Counter key, uint64_t amount=1) {
  if (!enabled) return;
  render_values[key] += amount;
  if (++render_pending[key] == 128) {
    totals[key].store(render_values[key], std::memory_order_relaxed);
    render_pending[key] = 0;
  }
}
// Allocation misses can occur on any decode worker. They are low frequency;
// keep a shared atomic rather than adding TLS lookups to every decoded vertex.
inline void SharedAdd(Counter key, uint64_t amount=1) {
  if (enabled) totals[key].fetch_add(amount, std::memory_order_relaxed);
}
inline void Frame() {
  if (enabled) totals[Frames].store(++render_values[Frames], std::memory_order_relaxed);
}
constexpr unsigned kPcSlots = 65536;
struct PcSlot { std::atomic<uint64_t> key{0}, calls{0}; };
inline PcSlot entry_pcs[kPcSlots], switch_pcs[kPcSlots];
inline uint64_t entry_values[kPcSlots]{}, switch_values[kPcSlots]{};
inline std::atomic<uint64_t> entry_overflow{0}, switch_overflow{0};
// Baselines are renderer-owned, like F4 export. Never reset producer totals or
// their pending batches from another thread. Window edges can straddle a batch.
inline uint64_t baseline[Count]{}, entry_baseline[kPcSlots]{}, switch_baseline[kPcSlots]{};
inline uint64_t entry_overflow_baseline=0, switch_overflow_baseline=0;
inline bool window_reset=false;
inline uint64_t Delta(uint64_t total,uint64_t start) { return total>=start?total-start:0; }
inline uint64_t Value(unsigned key) { return Delta(totals[key].load(std::memory_order_relaxed),baseline[key]); }
inline void Reset() {
  if (!enabled) return;
  for (unsigned i=0;i<Count;++i) baseline[i]=totals[i].load(std::memory_order_relaxed);
  if(pc_histograms)for (unsigned i=0;i<kPcSlots;++i) {
    entry_baseline[i]=entry_pcs[i].calls.load(std::memory_order_relaxed);
    switch_baseline[i]=switch_pcs[i].calls.load(std::memory_order_relaxed);
  }
  entry_overflow_baseline=entry_overflow.load(std::memory_order_relaxed);
  switch_overflow_baseline=switch_overflow.load(std::memory_order_relaxed);
  window_reset=true;
}
// One guest writer owns this sparse, direct PC-to-slot map. Exports only read
// the published slot atomics. Pages are allocated on first use; no allocation
// or table scan occurs on a subsequent PC hit, including former hash collisions.
struct PcIndex {
 static constexpr uint32_t Base=0x80000000u, End=0x81800000u;
 std::unique_ptr<uint32_t[]> pages[(End-Base)>>16];
 unsigned used=0;
 uint32_t Find(uint32_t pc) {
  if((pc&3u) || pc<Base || pc>=End)return UINT32_MAX;
  auto& page=pages[(pc-Base)>>16];
  if(!page)page=std::make_unique<uint32_t[]>(16384);
  auto& value=page[(pc&65535u)>>2];
  if(value)return value-1;
  if(used==kPcSlots)return UINT32_MAX;
  value=++used;return value-1;
 }
};
inline PcIndex entry_index,switch_index;
__attribute__((noinline, cold)) inline void RecordPcSlow(PcSlot* slots, uint64_t* values,
                                                    std::atomic<uint64_t>& overflow, uint32_t pc) {
  const uint64_t key = uint64_t(pc) + 1;
  const unsigned index=(slots==entry_pcs?entry_index:switch_index).Find(pc);
  if(index!=UINT32_MAX){
    if(!slots[index].key.load(std::memory_order_relaxed))slots[index].key.store(key,std::memory_order_release);
    slots[index].calls.store(++values[index],std::memory_order_relaxed);return;
  }
  overflow.fetch_add(1, std::memory_order_relaxed);
}
__attribute__((always_inline)) inline void RecordPc(PcSlot* slots, uint64_t* values,
                                                    std::atomic<uint64_t>& overflow, uint32_t pc) {
  if(!pc_histograms)return;
  RecordPcSlow(slots, values, overflow, pc);
}
__attribute__((noinline, cold)) inline void GuestEntrySlow(uint32_t pc, unsigned kind) {
  GuestAddSlow(kind==0 ? EntryNormal : kind==1 ? EntryResume : EntryContinuation, 1);
  RecordPc(entry_pcs, entry_values, entry_overflow, pc);
}
__attribute__((noinline, cold)) inline void GuestSwitchSlow(uint32_t pc, uint32_t cases) {
  GuestAddSlow(cases<=24 ? Switch24 : cases<=64 ? Switch64 : cases<=256 ? Switch256 : SwitchLarge, 1);
  RecordPc(switch_pcs, switch_values, switch_overflow, pc);
}
__attribute__((always_inline)) inline void GuestEntry(uint32_t pc, unsigned kind=0) {
  if (!enabled) return;
  GuestEntrySlow(pc, kind);
}
__attribute__((always_inline)) inline void GuestSwitch(uint32_t pc, uint32_t cases) {
  if (!enabled) return;
  GuestSwitchSlow(pc, cases);
}
}
