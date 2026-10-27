#pragma once
// NATIVE_GUEST_ARENA_V1: accessors that need the MMU declarations (write-gather sites, MMU-taking overloads).
// Kept apart from native_guest_arena.h because cpu_context.h includes that header before it declares the MMU.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_arena.h"

namespace native_arena {
using nwii::runtime::MMU;

// Overloads taking the MMU, so a generated `ctx.mmu.write32(a, v)` becomes `native_arena::st32(ctx.mmu, a, v)`.
__attribute__((always_inline)) inline void st8(MMU&, uint32_t a, uint8_t v) { st8(a, v); }
__attribute__((always_inline)) inline void st16(MMU&, uint32_t a, uint16_t v) { st16(a, v); }
__attribute__((always_inline)) inline void st32(MMU&, uint32_t a, uint32_t v) { st32(a, v); }
__attribute__((always_inline)) inline void st64(MMU&, uint32_t a, uint64_t v) { st64(a, v); }
__attribute__((always_inline)) inline void stf32(MMU&, uint32_t a, float v) { stf32(a, v); }
__attribute__((always_inline)) inline void stf64(MMU&, uint32_t a, double v) { stf64(a, v); }

// Statically recognised write-gather sites (displacement -32768 from a 0xCC010000 base, or a base known to be the
// pipe): the pipe page goes straight to the pipe, as the checked MMU path does, everything else is an ordinary
// arena store. The predicate is the checked path's: the 4 KiB page 0x0C008000 of any mirror, unless a watch
// overlaps the access (constant false in watch-folded builds).
__attribute__((always_inline)) inline bool wg_page(uint32_t a, unsigned bytes) {
  return __builtin_expect(a == 0xCC008000u || (a & 0x3ffff000u) == 0x0c008000u, 1) &&
         !nwii::runtime::native_watch_span_may_hit(a, bytes);
}
__attribute__((always_inline)) inline void fifo8(MMU&, uint32_t a, uint8_t v) {
  if (wg_page(a, 1)) { NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_Write8(v); return; }
  st8(a, v);
}
__attribute__((always_inline)) inline void fifo16(MMU&, uint32_t a, uint16_t v) {
  if (wg_page(a, 2)) { NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_Write16(v); return; }
  st16(a, v);
}
__attribute__((always_inline)) inline void fifo32(MMU&, uint32_t a, uint32_t v) {
  if (wg_page(a, 4)) { NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_Write32(v); return; }
  st32(a, v);
}
__attribute__((always_inline)) inline void fifof32(MMU&, uint32_t a, float v) {
  if (wg_page(a, 4)) { NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_WriteF32(v); return; }
  stf32(a, v);
}
__attribute__((always_inline)) inline void fifof64(MMU&, uint32_t a, double v) {
  if (wg_page(a, 8)) { NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_WriteF64(v); return; }
  stf64(a, v);
}
}  // namespace native_arena
