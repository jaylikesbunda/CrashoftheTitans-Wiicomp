#pragma once
// NATIVE_MEM_PROOF_V1: one address-classification proof per base register and straight-line region.
//
// tools/guest_mem_proofs.py groups the guest accesses that use one unchanged base register inside a
// straight-line region (no join label, call, poll or other ctx observer in between). The group's first
// access proves the whole byte span once; every access then either uses the proved fixed-base RAM
// address directly or, when the proof failed, its unchanged original accessor.
//
// What the proof establishes, for the span [physical + min, physical + end):
//   * it lies in one RAM bank: MEM1 above page 0 (page 0 holds the 0x0C special case and is never a
//     table-fast page), or MEM2;
//   * no watch can observe it: either no watch is configured, or the configured watch is still the one
//     the page table was built for and its 64 KiB page is outside the span (the MMU marks that page slow);
//   * so the original accessors would take their plain RAM path for every access of the group: the
//     native_fastmem reads, MMU::write* through page_bias, and native_guest_fast::write* (StackSpan).
// The bank map (page_bias) and g_watch_addr are only set at start-up, and a group never spans a call,
// poll or callback, so nothing can change them between the proof and the last access.
#include "runtime/cpu_context.h"
#include "runtime/native_fastmem.h"
// Include after runtime/native_guest_fast.h (the unit's own include defines NWII_V13_INLINE_INDIRECT first).
#include <cstdint>
#include <cstring>

namespace native_mem_proof {
using nwii::runtime::MMU;
inline constexpr uintptr_t kBase = 0x20000000u;  // NWII_STATIC_RAM_BASE: host = kBase + physical, both banks

__attribute__((always_inline)) inline bool Span(const MMU& mmu, uint32_t physical, int32_t lo, int32_t hi) {
  const int64_t a = int64_t(physical) + lo, b = int64_t(physical) + hi;
  const bool mem1 = a >= 0x10000 && b <= int64_t(MMU::kMem1Limit);
  const bool mem2 = a >= 0x10000000 && b <= int64_t(0x10000000u) + MMU::kMem2Limit;
  if (!(mem1 || mem2)) return false;
  const uint32_t watch = nwii::runtime::g_watch_addr;
  if (__builtin_expect(watch == 0, 1)) return mmu.native_v13_watch_snapshot == 0;
  if (watch != mmu.native_v13_watch_snapshot) return false;
  const int64_t page = int64_t((watch & 0x3fffffffu) & ~0xffffu);
  return b <= page || a >= page + 0x10000;
}

__attribute__((always_inline)) inline const void* At(uint32_t physical) { return reinterpret_cast<const void*>(kBase + physical); }
__attribute__((always_inline)) inline void* AtW(uint32_t physical) { return reinterpret_cast<void*>(kBase + physical); }

__attribute__((always_inline)) inline uint8_t read8(uint32_t p) { uint8_t v; std::memcpy(&v, At(p), 1); return v; }
__attribute__((always_inline)) inline uint16_t read16(uint32_t p) { uint16_t v; std::memcpy(&v, At(p), 2); return __builtin_bswap16(v); }
__attribute__((always_inline)) inline uint32_t read32(uint32_t p) { uint32_t v; std::memcpy(&v, At(p), 4); return __builtin_bswap32(v); }
__attribute__((always_inline)) inline uint64_t read64(uint32_t p) { uint64_t v; std::memcpy(&v, At(p), 8); return __builtin_bswap64(v); }
__attribute__((always_inline)) inline float read_f32(uint32_t p) { const uint32_t b = read32(p); float v; std::memcpy(&v, &b, 4); return v; }
__attribute__((always_inline)) inline double read_f64(uint32_t p) { const uint64_t b = read64(p); double v; std::memcpy(&v, &b, 8); return v; }

__attribute__((always_inline)) inline void write8(uint32_t p, uint8_t v) { std::memcpy(AtW(p), &v, 1); }
__attribute__((always_inline)) inline void write16(uint32_t p, uint16_t v) { v = __builtin_bswap16(v); std::memcpy(AtW(p), &v, 2); }
__attribute__((always_inline)) inline void write32(uint32_t p, uint32_t v) { v = __builtin_bswap32(v); std::memcpy(AtW(p), &v, 4); }
__attribute__((always_inline)) inline void write64(uint32_t p, uint64_t v) { v = __builtin_bswap64(v); std::memcpy(AtW(p), &v, 8); }
__attribute__((always_inline)) inline void write_f32(uint32_t p, float v) { uint32_t b; std::memcpy(&b, &v, 4); write32(p, b); }
__attribute__((always_inline)) inline void write_f64(uint32_t p, double v) { uint64_t b; std::memcpy(&b, &v, 8); write64(p, b); }

// Failed-proof paths: exactly the original accessors, kept out of line so a rewritten access adds only a
// predicted branch and a direct access to the hot code, not a second inline copy of the checked sequence.
#define NATIVE_MEM_PROOF_COLD(T, N) \
  __attribute__((noinline, cold)) inline T cold_read##N(MMU& m, uint32_t a) { return native_fastmem::read##N(m, a); } \
  __attribute__((noinline, cold)) inline void cold_fast_write##N(MMU& m, uint32_t a, T v) { native_guest_fast::write##N(m, a, v); } \
  __attribute__((noinline, cold)) inline void cold_mmu_write##N(MMU& m, uint32_t a, T v) { m.write##N(a, v); }
NATIVE_MEM_PROOF_COLD(uint8_t, 8)
NATIVE_MEM_PROOF_COLD(uint16_t, 16)
NATIVE_MEM_PROOF_COLD(uint32_t, 32)
NATIVE_MEM_PROOF_COLD(uint64_t, 64)
NATIVE_MEM_PROOF_COLD(float, _f32)
NATIVE_MEM_PROOF_COLD(double, _f64)
#undef NATIVE_MEM_PROOF_COLD
}
