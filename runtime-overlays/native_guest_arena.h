#pragma once
// NATIVE_GUEST_ARENA_V1: guest memory accessors over a host arena.
//
// A guest access is one instruction: `movbe reg,[base + index]` or `movbe [base + index],reg`. There is no bank test, no
// page table and no watch test; everything that is not RAM (MMIO, write-gather pipe, EFB, holes) is unmapped in the arena,
// so the access faults and runtime-overlays/native_guest_arena.cpp emulates it with the ordinary MMU slow paths. The
// instruction forms are fixed by inline assembly, so the fault handler decodes exactly six encodings.
//
// Layouts (compile-time, the same headers must see the same values in every translation unit):
//   default                     4 GiB arena [kBase, kBase + 4 GiB + 64 KiB) at the fixed address NWII_ARENA_FIXED_BASE.
//                               MEM1 and MEM2 are mapped (pagefile section views) at every 0x40000000 mirror, so the full
//                               32-bit guest address is the index and `addr & 0x3fffffff` is implicit. The base is an
//                               x86 disp32 (addressing `disp32(%reg)`), so it must be below 2 GiB and above the shared
//                               user data page and 64 KiB aligned: 0x7FFF0000.
//   NWII_ARENA_DYNAMIC_BASE=1   same layout at an address chosen by the OS at start-up and kept in `g_base` (a const object,
//                               so the compiler keeps it in one register); costs a register, never fails to place.
//   NWII_ARENA_MASK=1           the older 1 GiB window at 0x20000000 (VirtualAlloc, MEM_WRITE_WATCH so GetWriteWatch keeps
//                               working); the index is `addr & 0x3fffffff` (one extra AND per access).
// In every layout MMU::mem1/mem2 stay at 0x20000000 / 0x30000000 (a second view of the same section when the arena is
// not the window itself), so each page_bias based helper needs no change and all views see identical bytes.
#include <cstdint>
#include <cstring>

#ifndef NWII_ARENA_MASK
#define NWII_ARENA_MASK 0
#endif
#ifndef NWII_ARENA_DYNAMIC_BASE
#define NWII_ARENA_DYNAMIC_BASE 0
#endif
#ifndef NWII_ARENA_FIXED_BASE
#define NWII_ARENA_FIXED_BASE 0x7FFF0000u
#endif
#if NWII_ARENA_MASK && NWII_ARENA_DYNAMIC_BASE
#error "NWII_ARENA_MASK and NWII_ARENA_DYNAMIC_BASE are exclusive"
#endif

#if NWII_ARENA_MASK
// The window is shared with host code, so guest accesses carry a redundant DS override (ignored in 64-bit mode, one byte)
// that tells the fault handler which accesses to emulate. A separate arena needs no marker: only guest accesses touch it.
#define NWII_ARENA_DS ".byte 0x3e\n\t"
#else
#define NWII_ARENA_DS ""
#endif
// Float stores carry a CS override (also ignored). Integer and float stores of the same width differ in the MMU's slow paths
// (word 12 drops only the integer store, the write-gather pipe takes float64 but not int64, hardware registers ignore floats),
// and the fault handler must reproduce the one the generated code asked for.
#define NWII_ARENA_FP ".byte 0x2e\n\t"

namespace native_arena {
inline constexpr uintptr_t kWindow1 = 0x20000000u, kWindow2 = 0x30000000u;   // MMU::mem1 / mem2
inline constexpr uint32_t kMem1 = 24u * 1024u * 1024u, kMem2 = 64u * 1024u * 1024u;
inline constexpr uint64_t kSpan = NWII_ARENA_MASK ? 0x40010000ull : 0x100010000ull;
#if NWII_ARENA_MASK
inline constexpr uintptr_t kBase = kWindow1;
__attribute__((always_inline)) inline uintptr_t base() { return kBase; }
#elif NWII_ARENA_DYNAMIC_BASE
extern const uintptr_t g_base;
__attribute__((always_inline)) inline uintptr_t base() { return g_base; }
#else
inline constexpr uintptr_t kBase = NWII_ARENA_FIXED_BASE;
static_assert(kBase >= 0x7FFF0000u && kBase <= 0x7FFF0000u && (kBase & 0xffffu) == 0, "disp32 base: 64 KiB aligned, above the shared user data page, below 2 GiB");
__attribute__((always_inline)) inline uintptr_t base() { return kBase; }
#endif

// Host address of a guest address. A constant address is hidden from the compiler: the same bytes are also reachable through
// the MMU window (0x20000000 / 0x30000000) and through other mirrors, and a compiler that sees two different constant
// addresses would assume they never alias (and forward a stale value between them). Variable addresses need no help: an
// access through `base + x` may alias any other pointer. A constant needs a register for the index anyway (the arena address
// of a guest address above 0x80000000 does not fit a 32-bit displacement), so the barrier costs nothing.
__attribute__((always_inline)) inline uintptr_t at(uint32_t address) {
  if (__builtin_constant_p(address)) __asm__("" : "+r"(address));
#if NWII_ARENA_MASK
  return base() + (address & 0x3fffffffu);
#else
  return base() + address;
#endif
}

// ---- loads: non-volatile (values may be reused), memory-dependent through the "m" operand ----
__attribute__((always_inline)) inline uint8_t ld8(uint32_t a) {
  uint32_t v;
  __asm__(NWII_ARENA_DS "movzbl %1, %0" : "=r"(v) : "m"(*reinterpret_cast<const uint8_t*>(at(a))));
  return uint8_t(v);
}
__attribute__((always_inline)) inline uint16_t ld16(uint32_t a) {
  uint16_t v;
  __asm__(NWII_ARENA_DS "movbe %1, %0" : "=r"(v) : "m"(*reinterpret_cast<const uint16_t*>(at(a))));
  return v;
}
__attribute__((always_inline)) inline uint32_t ld32(uint32_t a) {
  uint32_t v;
  __asm__(NWII_ARENA_DS "movbe %1, %0" : "=r"(v) : "m"(*reinterpret_cast<const uint32_t*>(at(a))));
  return v;
}
__attribute__((always_inline)) inline uint64_t ld64(uint32_t a) {
  uint64_t v;
  __asm__(NWII_ARENA_DS "movbe %1, %0" : "=r"(v) : "m"(*reinterpret_cast<const uint64_t*>(at(a))));
  return v;
}
__attribute__((always_inline)) inline float ldf32(uint32_t a) {
  const uint32_t b = ld32(a); float f; std::memcpy(&f, &b, 4); return f;
}
__attribute__((always_inline)) inline double ldf64(uint32_t a) {
  const uint64_t b = ld64(a); double d; std::memcpy(&d, &b, 8); return d;
}

// ---- stores: volatile asm (a store is never dropped or duplicated), register source only ----
__attribute__((always_inline)) inline void st8(uint32_t a, uint8_t v) {
  __asm__ volatile(NWII_ARENA_DS "movb %1, %0" : "=m"(*reinterpret_cast<uint8_t*>(at(a))) : "q"(v));
}
__attribute__((always_inline)) inline void st16(uint32_t a, uint16_t v) {
  __asm__ volatile(NWII_ARENA_DS "movbe %1, %0" : "=m"(*reinterpret_cast<uint16_t*>(at(a))) : "r"(v));
}
__attribute__((always_inline)) inline void st32(uint32_t a, uint32_t v) {
  __asm__ volatile(NWII_ARENA_DS "movbe %1, %0" : "=m"(*reinterpret_cast<uint32_t*>(at(a))) : "r"(v));
}
__attribute__((always_inline)) inline void st64(uint32_t a, uint64_t v) {
  __asm__ volatile(NWII_ARENA_DS "movbe %1, %0" : "=m"(*reinterpret_cast<uint64_t*>(at(a))) : "r"(v));
}
__attribute__((always_inline)) inline void stf32(uint32_t a, float v) {
  uint32_t b; std::memcpy(&b, &v, 4);
  __asm__ volatile(NWII_ARENA_DS NWII_ARENA_FP "movbe %1, %0" : "=m"(*reinterpret_cast<uint32_t*>(at(a))) : "r"(b));
}
__attribute__((always_inline)) inline void stf64(uint32_t a, double v) {
  uint64_t b; std::memcpy(&b, &v, 8);
  __asm__ volatile(NWII_ARENA_DS NWII_ARENA_FP "movbe %1, %0" : "=m"(*reinterpret_cast<uint64_t*>(at(a))) : "r"(b));
}

// ---- runtime control (native_guest_arena.cpp) ----
// Creates the arena (idempotent). Runs at start-up and from native_static_ram_commit.
void init();
bool active();
// Re-reads g_watch_addr and the page-0 policy and (un)protects the pages that must trap guest stores.
void sync_watch();
// RAII scope for host code that hands a guest RAM range to the kernel (fread/ReadFile): pages that trap stores are
// made writable for the scope; the kernel cannot recover from a protection fault, a user-mode write can.
struct HostWriteScope {
  void* range; size_t bytes; unsigned saved;
  HostWriteScope(void* pointer, size_t count);
  ~HostWriteScope();
  HostWriteScope(const HostWriteScope&) = delete;
  HostWriteScope& operator=(const HostWriteScope&) = delete;
};
struct Stats {
  uint64_t loads = 0, stores = 0, mmio = 0, wgpipe = 0, sticky = 0, tracker = 0, unhandled = 0, sites = 0;
};
Stats stats();
// Writes the fault-site report (rva kind width guest_address write_gather count ctx_pc ctx_lr per host instruction) to `path`; the
// same report is written at exit when NWII_ARENA_SITES is set. tools/fix_guest_arena.py --map-sites turns it into a function list.
void report_sites(const char* path);
// Test hook: called with every emulated access when set (kind 0 load, 1 store).
using Observer = void (*)(int kind, uint32_t guest_address, unsigned width, uint64_t value);
void set_observer(Observer);
}  // namespace native_arena
