// NATIVE_GUEST_ARENA_V1: host arena for guest memory, fault emulation of everything that is not RAM, store traps.
//
// Layout (see native_guest_arena.h):
//   NWII_ARENA_MASK=0  one pagefile section per bank, mapped at every 0x40000000 mirror of a 4 GiB reservation (fixed base
//                      or OS-chosen base, see the header) and a second time at the addresses MMU::mem1/mem2 use
//                      (0x20000000, 0x30000000). All views alias the same pages.
//   NWII_ARENA_MASK=1  the older 1 GiB VirtualAlloc window (MEM_WRITE_WATCH kept so GetWriteWatch still works).
//
// Everything outside the two banks is PAGE_NOACCESS. The vectored handler decodes the access, runs the ordinary MMU slow
// path (MMIO, write-gather pipe, IPC, watch) and resumes after the instruction. The slow path may longjmp out of the
// handler exactly as it did out of the checked inline accessors.
//
// Pages that must trap guest *stores* although they are RAM ("sticky" pages: the debug watch page, and physical page 0
// whose word 12 reads as zero and drops stores) are read-only in every view. The handler applies the watch hit / the
// drop, then lets the original store run for one instruction with the page temporarily writable (trap flag), or skips it.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_arena.h"
#include <windows.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <mutex>
#include <thread>
#include <vector>

namespace nwii::runtime {
extern MMU* g_mmu;
uint64_t native_ram_generation();
uint64_t native_ram_newest_write(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes);
extern CPUContext* g_ctx_ptr;
}

namespace native_arena {
using namespace nwii::runtime;
namespace {

// ---- statistics, observers, fault-site recorder ------------------------------------------------------------------
std::atomic<uint64_t> n_loads{0}, n_stores{0}, n_mmio{0}, n_wg{0}, n_sticky{0}, n_tracker{0}, n_unhandled{0};
Observer g_observer = nullptr;

struct Site {
  std::atomic<uintptr_t> rip{0};
  std::atomic<uint64_t> count{0};
  uint32_t address = 0, pc = 0, lr = 0;
  uint8_t kind = 0, width = 0;
  bool wg = false;
};
constexpr unsigned kSites = 8192;
Site g_sites[kSites];
std::atomic<uint64_t> g_site_count{0};

void record_site(uintptr_t rip, int kind, unsigned width, uint32_t address) {
  unsigned slot = unsigned((rip * 0x9E3779B97F4A7C15ull) >> 51) & (kSites - 1);
  for (unsigned probe = 0; probe < kSites; ++probe, slot = (slot + 1) & (kSites - 1)) {
    Site& s = g_sites[slot];
    uintptr_t seen = s.rip.load(std::memory_order_acquire);
    if (seen == 0) {
      uintptr_t expected = 0;
      if (s.rip.compare_exchange_strong(expected, rip, std::memory_order_acq_rel)) {
        s.address = address; s.kind = uint8_t(kind); s.width = uint8_t(width);
        s.wg = (address & 0x3ffff000u) == 0x0c008000u;
        if (const CPUContext* c = g_ctx_ptr) { s.pc = c->pc; s.lr = c->lr; }
        g_site_count.fetch_add(1, std::memory_order_relaxed);
        s.count.fetch_add(1, std::memory_order_relaxed);
        return;
      }
      seen = expected;
    }
    if (seen == rip) { s.count.fetch_add(1, std::memory_order_relaxed); return; }
  }
}

void write_report_to(const char* path) {
  if (!path || !*path) return;
  std::FILE* f = std::fopen(path, "w");
  if (!f) return;
  const uintptr_t module = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
  std::fprintf(f, "# faults: loads=%llu stores=%llu (hardware %llu, write-gather %llu) store traps (page 0 / watch)=%llu tracker=%llu unhandled=%llu\n",
               static_cast<unsigned long long>(n_loads.load()), static_cast<unsigned long long>(n_stores.load()), static_cast<unsigned long long>(n_mmio.load()),
               static_cast<unsigned long long>(n_wg.load()), static_cast<unsigned long long>(n_sticky.load()), static_cast<unsigned long long>(n_tracker.load()),
               static_cast<unsigned long long>(n_unhandled.load()));
  std::fprintf(f, "# rva kind width guest_address write_gather count ctx_pc ctx_lr\n");
  for (const Site& s : g_sites) {
    const uintptr_t rip = s.rip.load();
    if (!rip) continue;
    std::fprintf(f, "0x%llx %s %u 0x%08x %d %llu 0x%08x 0x%08x\n", static_cast<unsigned long long>(rip - module),
                 s.kind ? "store" : "load", s.width, s.address, s.wg ? 1 : 0,
                 static_cast<unsigned long long>(s.count.load()), s.pc, s.lr);
  }
  std::fclose(f);
}
void write_report() { write_report_to(std::getenv("NWII_ARENA_SITES")); }

// ---- Win32 plumbing -------------------------------------------------------------------------------------------------
using PFN_VirtualAlloc2 = PVOID(WINAPI*)(HANDLE, PVOID, SIZE_T, ULONG, ULONG, void*, ULONG);
using PFN_MapViewOfFile3 = PVOID(WINAPI*)(HANDLE, HANDLE, PVOID, ULONG64, SIZE_T, ULONG, ULONG, void*, ULONG);
// MEM_EXTENDED_PARAMETER / MEM_ADDRESS_REQUIREMENTS (the dynamic layout asks for a base above 4 GiB, clear of the fixed MMU windows).
struct AddressRequirements { PVOID lowest; PVOID highest; SIZE_T alignment; };
struct ExtendedParameter { DWORD64 type; PVOID value; };
constexpr DWORD64 kAddressRequirementsType = 1;
constexpr ULONG kReservePlaceholder = 0x00040000, kReplacePlaceholder = 0x00004000, kPreservePlaceholder = 0x2;

std::once_flag g_once;
bool g_active = false;
bool g_page0 = true;
uintptr_t g_base_value = 0;      // run-time base (equals kBase in the fixed layouts)
HANDLE g_section[2] = {nullptr, nullptr};
uint8_t* g_priv[2] = {nullptr, nullptr};   // a second, never protected view of each bank: sticky-page stores are emulated through it
constexpr unsigned kQuadrants = NWII_ARENA_MASK ? 1u : 4u;
uintptr_t arena_base() { return g_base_value; }

[[noreturn]] void die(const char* what, unsigned long error) {
  std::fprintf(stderr, "[Arena] %s (error %lu). Build without NWII_BUILD_GUEST_ARENA to use the checked accessors.\n", what, error);
  std::fflush(stderr);
  std::abort();
}

uintptr_t view_address(unsigned quadrant, unsigned bank, uint32_t offset) {
  return arena_base() + uintptr_t(quadrant) * 0x40000000u + (bank ? 0x10000000u : 0u) + offset;
}
// The views MMU::mem1/mem2 use (identical to quadrant 0 of the window layout).
uintptr_t window_address(unsigned bank, uint32_t offset) { return (bank ? kWindow2 : kWindow1) + offset; }

// ---- sticky pages ----------------------------------------------------------------------------------------------------
struct Sticky { uint32_t phys_page; uint8_t kind; };  // phys_page: physical address >> 12 (MEM1 below 0x1800, MEM2 at 0x10000)
enum : uint8_t { kPage0 = 1, kWatch = 2 };
Sticky g_sticky[4];
std::atomic<unsigned> g_sticky_count{0};
std::mutex g_sticky_mutex;

bool ram_phys(uint32_t phys) { return phys < kMem1 || phys - 0x10000000u < kMem2; }
// Address of the 4 KiB page of `phys` in view number `view` (0..kQuadrants-1 arena quadrants, then the MMU window).
constexpr unsigned kViews = NWII_ARENA_MASK ? 1u : 5u;
uintptr_t page_view(unsigned view, uint32_t phys) {
  const unsigned bank = phys >= 0x10000000u;
  const uint32_t offset = (bank ? phys - 0x10000000u : phys) & ~0xfffu;
  return view < kQuadrants ? view_address(view, bank, offset) : window_address(bank, offset);
}
void protect_page(uint32_t phys, DWORD protection) {
  for (unsigned v = 0; v < kViews; ++v) {
    DWORD old;
    VirtualProtect(reinterpret_cast<void*>(page_view(v, phys)), 4096, protection, &old);
  }
}

// Which view an address belongs to: the arena (guest accessors) or the MMU window (host code).
struct Region { bool arena; uint32_t guest; uint32_t phys; };
bool classify(uintptr_t address, Region& r) {
  const uintptr_t b = arena_base();
  if (address >= b && address < b + kSpan) {
    r.arena = true; r.guest = uint32_t(address - b); r.phys = r.guest & 0x3fffffffu;
    return true;
  }
#if !NWII_ARENA_MASK
  if (address >= kWindow1 && address < kWindow1 + kMem1 + 0x10000u) { r.arena = false; r.guest = r.phys = uint32_t(address - kWindow1); return true; }
  if (address >= kWindow2 && address < kWindow2 + kMem2 + 0x10000u) { r.arena = false; r.phys = 0x10000000u + uint32_t(address - kWindow2); r.guest = r.phys; return true; }
#endif
  return false;
}

// ---- instruction decoding ----------------------------------------------------------------------------------------------
uint64_t* reg_ptr(CONTEXT* c, unsigned r) {
  switch (r) {
    case 0: return &c->Rax; case 1: return &c->Rcx; case 2: return &c->Rdx; case 3: return &c->Rbx;
    case 4: return &c->Rsp; case 5: return &c->Rbp; case 6: return &c->Rsi; case 7: return &c->Rdi;
    case 8: return &c->R8; case 9: return &c->R9; case 10: return &c->R10; case 11: return &c->R11;
    case 12: return &c->R12; case 13: return &c->R13; case 14: return &c->R14; default: return &c->R15;
  }
}

enum class Kind { None, Load, Store };
struct Access {
  Kind kind = Kind::None;
  unsigned length = 0, width = 0, reg = 0;   // width in bytes, reg 0..15
  bool movbe = false;                        // register holds the guest (big-endian) numeric value
  bool sign = false, zero = false;           // load extension of narrow results
  bool rex = false;                          // 8-bit register naming (spl/bpl/sil/dil need REX)
  bool guest = false;                        // carries the DS override of native_guest_arena.h
  bool fp = false;                           // carries the CS override: a float store (stfs/stfd)
  unsigned dest_bits = 32;
  uintptr_t ea = 0;
};

// Exactly the encodings native_guest_arena.h emits (movzx r32,m8 / movbe r16|r32|r64,m / movbe m,r16|r32|r64 / mov m8,r8)
// plus the plain mov/movzx/movsx loads older fast paths may still produce.
bool decode(const uint8_t* ip, CONTEXT* c, Access& out) {
  const uint8_t* p = ip;
  bool opsize = false, addr32 = false;
  unsigned rex = 0;
  for (int i = 0; i < 6; ++i) {
    if (*p == 0x66) { opsize = true; ++p; continue; }
    if (*p == 0x67) { addr32 = true; ++p; continue; }
    if (*p == 0x3E) { out.guest = true; ++p; continue; }
    if (*p == 0x2E) { out.fp = true; ++p; continue; }
    if ( *p == 0x26 || *p == 0x36 || *p == 0x64 || *p == 0x65 || *p == 0xF2 || *p == 0xF3) { ++p; continue; }
    break;
  }
  if ((*p & 0xF0) == 0x40) { rex = *p; out.rex = true; ++p; }
  const bool w = rex & 8, r = rex & 4, x = rex & 2, b = rex & 1;
  const unsigned osize = w ? 64 : opsize ? 16 : 32;
  out.dest_bits = osize; out.width = osize / 8;
  if (p[0] == 0x0F && p[1] == 0x38 && p[2] == 0xF0) { out.kind = Kind::Load; out.movbe = true; p += 3; }
  else if (p[0] == 0x0F && p[1] == 0x38 && p[2] == 0xF1) { out.kind = Kind::Store; out.movbe = true; p += 3; }
  else if (p[0] == 0x0F && (p[1] == 0xB6 || p[1] == 0xB7 || p[1] == 0xBE || p[1] == 0xBF)) {
    out.kind = Kind::Load; out.width = (p[1] & 1) ? 2 : 1; out.sign = p[1] >= 0xBE; out.zero = !out.sign; p += 2;
  }
  else if (p[0] == 0x88) { out.kind = Kind::Store; out.movbe = true; out.width = 1; out.dest_bits = 8; p += 1; }
  else if (p[0] == 0x8A) { out.kind = Kind::Load; out.width = 1; out.dest_bits = 8; p += 1; }
  else if (p[0] == 0x8B) { out.kind = Kind::Load; p += 1; }
  else if (p[0] == 0x63 && w) { out.kind = Kind::Load; out.width = 4; out.sign = true; p += 1; }
  else return false;
  const uint8_t modrm = *p++;
  const unsigned mod = modrm >> 6, rm = modrm & 7;
  out.reg = ((modrm >> 3) & 7) | (r ? 8 : 0);
  if (mod == 3) return false;
  auto regv = [&](unsigned n) -> uint64_t { const uint64_t v = *reg_ptr(c, n); return addr32 ? (v & 0xFFFFFFFFu) : v; };
  uint64_t ea = 0;
  if (rm == 4) {
    const uint8_t sib = *p++;
    const unsigned scale = 1u << (sib >> 6), index = ((sib >> 3) & 7) | (x ? 8 : 0), base = (sib & 7) | (b ? 8 : 0);
    if (index != 4) ea += regv(index) * scale;
    if ((sib & 7) == 5 && mod == 0) { int32_t d; std::memcpy(&d, p, 4); p += 4; ea += uint64_t(int64_t(d)); }
    else ea += regv(base);
  } else if (rm == 5 && mod == 0) {
    return false;  // RIP-relative: never a guest access
  } else {
    ea += regv(rm | (b ? 8 : 0));
  }
  if (mod == 1) { ea += uint64_t(int64_t(int8_t(*p))); p += 1; }
  else if (mod == 2) { int32_t d; std::memcpy(&d, p, 4); p += 4; ea += uint64_t(int64_t(d)); }
  if (addr32) ea &= 0xFFFFFFFFu;
  out.ea = uintptr_t(ea);
  out.length = unsigned(p - ip);
  return true;
}

// Value of the source register of a store, narrowed to its width.
uint64_t source_value(CONTEXT* c, const Access& a) {
  if (a.width == 1) {
    if (!a.rex && a.reg >= 4 && a.reg < 8) return (*reg_ptr(c, a.reg - 4) >> 8) & 0xFF;
    return *reg_ptr(c, a.reg) & 0xFF;
  }
  const uint64_t v = *reg_ptr(c, a.reg);
  return a.width == 2 ? (v & 0xFFFF) : a.width == 4 ? (v & 0xFFFFFFFFu) : v;
}

void write_dest(CONTEXT* c, const Access& l, uint64_t v) {
  if (l.dest_bits == 8) {
    if (!l.rex && l.reg >= 4 && l.reg < 8) { uint64_t* d = reg_ptr(c, l.reg - 4); *d = (*d & ~uint64_t(0xFF00)) | ((v & 0xFF) << 8); }
    else { uint64_t* d = reg_ptr(c, l.reg); *d = (*d & ~uint64_t(0xFF)) | (v & 0xFF); }
  } else if (l.dest_bits == 16) {
    uint64_t* d = reg_ptr(c, l.reg); *d = (*d & ~uint64_t(0xFFFF)) | (v & 0xFFFF);
  } else if (l.dest_bits == 32) {
    *reg_ptr(c, l.reg) = v & 0xFFFFFFFFu;  // 32-bit writes zero the upper half, also for movzx/movbe r32
  } else {
    *reg_ptr(c, l.reg) = v;
  }
}

// ---- emulation through the ordinary MMU slow paths ---------------------------------------------------------------------
uint64_t slow_read(uint32_t guest, unsigned width) {  // big-endian numeric value
  MMU& m = *g_mmu;
  switch (width) {
    case 1: return m.native_read8_slow(guest);
    case 2: return m.native_read16_slow(guest);
    case 4: return m.native_read32_slow(guest);
    default: return m.native_read64_slow(guest);
  }
}
void slow_write(uint32_t guest, unsigned width, uint64_t value, bool fp) {
  MMU& m = *g_mmu;
  switch (width) {
    case 1: m.native_write8_slow(guest, uint8_t(value)); break;
    case 2: m.native_write16_slow(guest, uint16_t(value)); break;
    case 4:
      if (fp) { const uint32_t bits = uint32_t(value); float f; std::memcpy(&f, &bits, 4); m.native_write_f32_slow(guest, f); }
      else m.native_write32_slow(guest, uint32_t(value));
      break;
    default:
      if (fp) { double d; std::memcpy(&d, &value, 8); m.native_write_f64_slow(guest, d); }
      else m.native_write64_slow(guest, value);
      break;
  }
}

// ---- store traps -----------------------------------------------------------------------------------------------------
struct PendingStep { uintptr_t page[2]; unsigned count = 0; DWORD old[2]; };
thread_local PendingStep t_step;

// Sticky pages trap guest (arena) stores only: host code reaches the same bytes through the MMU window, whose software paths already apply
// the page-0 and watch rules, so that view stays writable and a checked store never raises an exception.
void protect_sticky(uint32_t phys, DWORD protection) {
  for (unsigned v = 0; v < kQuadrants; ++v) {
    DWORD old;
    VirtualProtect(reinterpret_cast<void*>(page_view(v, phys)), 4096, protection, &old);
  }
}

bool sticky_page(uint32_t phys, uint8_t* kind) {
  const unsigned n = g_sticky_count.load(std::memory_order_acquire);
  for (unsigned i = 0; i < n; ++i)
    if (g_sticky[i].phys_page == (phys >> 12)) { *kind = g_sticky[i].kind; return true; }
  return false;
}

// Watch predicate of the original slow paths: a word for 8/16/32-bit stores, a doubleword for 64-bit stores.
bool watch_hits(uint32_t phys, unsigned width) {
  const uint32_t w = g_watch_addr;
  if (!w) return false;
  return width == 8 ? (phys & ~7u) == (w & 0x3FFFFFF8u) : (phys & ~3u) == (w & 0x3FFFFFFCu);
}

// ---- tracker (mask 0 only) --------------------------------------------------------------------------------------------
// MEM_WRITE_WATCH cannot be requested for a section view, so GetWriteWatch is not available for the mirrored arena. The
// replacement write-protects the pages the texture and geometry caches ask about (in every view) and records the first
// write to each as a dirty flag; native_ram_newest_write folds those flags into page generations exactly like the
// write-watch version. Pages written repeatedly are reported as untracked so the caller compares bytes instead.
struct Tracker {
  static constexpr unsigned kPages1 = 6144, kPages2 = 16384;
  std::atomic<uint8_t> armed[kPages1 + kPages2];
  std::atomic<uint8_t> dirty[kPages1 + kPages2];
  uint64_t generation[kPages1 + kPages2];
  uint8_t faults[kPages1 + kPages2];
  // NATIVE_TEXTURE_VALIDATION_MEMO_V1 (per-page epochs): the epoch of the last write that reached each page. Values come from
  // g_write_epoch, so a page's epoch only grows and a fresh value exceeds every value handed out before.
  std::atomic<uint64_t> page_epoch[kPages1 + kPages2];
  uint64_t queries = 0;
  Tracker() { for (auto& a : armed) a = 0; for (auto& d : dirty) d = 0; for (auto& e : page_epoch) e = 0; std::memset(generation, 0, sizeof generation); std::memset(faults, 0, sizeof faults); }
};
Tracker* g_tracker = nullptr;
std::atomic<uint64_t> g_generation{1};
// NATIVE_TEXTURE_VALIDATION_MEMO_V1: advances on every write that reaches an armed page (fault or host scope). Each advance
// stamps the written page with the new value (Tracker::page_epoch).
std::atomic<uint64_t> g_write_epoch{1};
std::atomic<uint64_t> g_pages_armed_total{0};
std::mutex g_track_mutex;
bool g_track_enabled = false;
unsigned tracker_index(uint32_t phys) { return phys < kMem1 ? phys >> 12 : Tracker::kPages1 + ((phys - 0x10000000u) >> 12); }
uint64_t next_write_epoch() { return g_write_epoch.fetch_add(1, std::memory_order_acq_rel) + 1; }

void tracker_unprotect(uint32_t phys) { protect_page(phys & ~0xfffu, PAGE_READWRITE); }

bool tracker_fault(uint32_t phys) {
  if (!g_tracker) return false;
  const unsigned i = tracker_index(phys);
  if (!g_tracker->armed[i].load(std::memory_order_acquire)) return false;
  tracker_unprotect(phys);                      // writable first, then publish: a query that sees the flag re-arms safely
  g_tracker->armed[i].store(0, std::memory_order_release);
  g_tracker->dirty[i].store(1, std::memory_order_release);
  g_tracker->page_epoch[i].store(next_write_epoch(), std::memory_order_release);
  n_tracker.fetch_add(1, std::memory_order_relaxed);
  return true;
}

bool g_debug = false;
LONG decline(const char* why, EXCEPTION_POINTERS* ep) {
  if (g_debug) std::fprintf(stderr, "[Arena] declined: %s rip=%p fault=%p\n", why, reinterpret_cast<void*>(ep->ContextRecord->Rip),
                            reinterpret_cast<void*>(ep->ExceptionRecord->NumberParameters > 1 ? ep->ExceptionRecord->ExceptionInformation[1] : 0));
  return EXCEPTION_CONTINUE_SEARCH;
}
// ---- the handler -----------------------------------------------------------------------------------------------------
LONG handle_step(EXCEPTION_POINTERS* ep) {
  if (!t_step.count) return EXCEPTION_CONTINUE_SEARCH;
  for (unsigned i = 0; i < t_step.count; ++i) {
    DWORD old;
    VirtualProtect(reinterpret_cast<void*>(t_step.page[i]), 4096, t_step.old[i], &old);
  }
  t_step.count = 0;
  ep->ContextRecord->EFlags &= ~0x100u;
  return EXCEPTION_CONTINUE_EXECUTION;
}

LONG CALLBACK arena_handler(EXCEPTION_POINTERS* ep) {
  const EXCEPTION_RECORD* rec = ep->ExceptionRecord;
  if (rec->ExceptionCode == EXCEPTION_SINGLE_STEP) return handle_step(ep);
  if (rec->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || rec->NumberParameters < 2 || !g_mmu) return EXCEPTION_CONTINUE_SEARCH;
  const uintptr_t fault = rec->ExceptionInformation[1];
  const bool is_write = rec->ExceptionInformation[0] == 1;
  Region fr;
  if (rec->ExceptionInformation[0] > 1 || !classify(fault, fr)) return g_debug ? decline("outside the arena", ep) : EXCEPTION_CONTINUE_SEARCH;
  CONTEXT* c = ep->ContextRecord;
  Access a;
  Region er{}, last{};
  const bool decoded = decode(reinterpret_cast<const uint8_t*>(c->Rip), c, a) && a.kind != Kind::None &&
                       (a.width == 1 || a.width == 2 || a.width == 4 || a.width == 8) && (a.kind == Kind::Store) == is_write &&
                       classify(a.ea, er) && classify(a.ea + a.width - 1, last) && er.arena == last.arena;
  // A guest access is one that goes through the arena (mask layout: one that carries the marker).
#if NWII_ARENA_MASK
  const bool guest_access = decoded && a.guest;
#else
  const bool guest_access = decoded && er.arena;
#endif

  if (is_write && ram_phys(fr.phys)) {
    uint8_t kind = 0;
    // Tracker pages and sticky pages belong to RAM; every other fault inside RAM is a straddling or stray access that the
    // slow path below resolves.
    if (tracker_fault(fr.phys)) return EXCEPTION_CONTINUE_EXECUTION;
    if (sticky_page(fr.phys, &kind)) {
      n_sticky.fetch_add(1, std::memory_order_relaxed);
      if (guest_access) {
        const uint32_t guest = er.guest, phys = er.phys;
        const uint64_t value = source_value(c, a);
        record_site(c->Rip, 1, a.width, guest);   // sticky-page stores (page 0 / watch) show up in the fault report too
        if ((kind & kPage0) && a.width == 4 && phys == 12u) {  // MMU::write32 drops word 12 (write_f32 keeps it; see the notes: the word then reads as 0 either way)
          c->Rip += a.length;
          return EXCEPTION_CONTINUE_EXECUTION;
        }
        if ((kind & kWatch) && watch_hits(phys, a.width)) {
          const uint64_t v = a.width == 8 ? value >> 32 : value;
          watch_hit(guest, uint32_t(v), a.width == 8 ? 64 : int(a.width) * 8);
        }
      }
      // Emulate the store through the private view: no VirtualProtect, no single-step exception (two syscalls and an exception less).
      if (guest_access && a.kind == Kind::Store && (a.width == 1 || a.width == 2 || a.width == 4 || a.width == 8)) {
        const uint32_t phys = er.phys;
        const unsigned bank = phys >= 0x10000000u;
        if (g_priv[bank] && (phys & 0xfffu) + a.width <= 0x1000u) {
          uint8_t* dst = g_priv[bank] + (bank ? phys - 0x10000000u : phys);
          const uint64_t v = source_value(c, a);
          if (a.width == 1) *dst = uint8_t(v);
          else if (a.width == 2) { const uint16_t s = __builtin_bswap16(uint16_t(v)); std::memcpy(dst, &s, 2); }
          else if (a.width == 4) { const uint32_t s = __builtin_bswap32(uint32_t(v)); std::memcpy(dst, &s, 4); }
          else { const uint64_t s = __builtin_bswap64(v); std::memcpy(dst, &s, 8); }
          c->Rip += a.length;
          return EXCEPTION_CONTINUE_EXECUTION;
        }
      }
      // Let the original store run: the faulting page is writable for exactly one instruction.
      if (t_step.count >= 2) return decline("step overflow", ep);
      const uintptr_t page = fault & ~uintptr_t(0xfff);
      DWORD old;
      if (!VirtualProtect(reinterpret_cast<void*>(page), 4096, PAGE_READWRITE, &old)) return decline("unprotect failed", ep);
      t_step.page[t_step.count] = page; t_step.old[t_step.count] = old; ++t_step.count;
      c->EFlags |= 0x100u;
      return EXCEPTION_CONTINUE_EXECUTION;
    }
  }
  if (!guest_access) { n_unhandled.fetch_add(1, std::memory_order_relaxed); return decline("not a guest access", ep); }

  const uint32_t guest = er.guest;
  record_site(c->Rip, a.kind == Kind::Store, a.width, guest);
  if (a.kind == Kind::Load) {
    uint64_t value = slow_read(guest, a.width);
    if (!a.movbe) {  // a plain mov loaded the bytes little-endian: hand it the byte-swapped numeric value
      if (a.width == 2) value = __builtin_bswap16(uint16_t(value));
      else if (a.width == 4) value = __builtin_bswap32(uint32_t(value));
      else if (a.width == 8) value = __builtin_bswap64(value);
    }
    if (a.sign) value = a.width == 1 ? uint64_t(int64_t(int8_t(value))) : a.width == 2 ? uint64_t(int64_t(int16_t(value))) : uint64_t(int64_t(int32_t(value)));
    else if (a.zero) value &= a.width == 1 ? 0xffu : 0xffffu;
    if (g_observer) g_observer(0, guest, a.width, value);
    n_loads.fetch_add(1, std::memory_order_relaxed);
    write_dest(c, a, value);
    c->Rip += a.length;
    return EXCEPTION_CONTINUE_EXECUTION;
  }
  const uint64_t value = source_value(c, a);
  if (g_observer) g_observer(1, guest, a.width, value);
  n_stores.fetch_add(1, std::memory_order_relaxed);
  if ((guest & 0x3ffff000u) == 0x0c008000u) n_wg.fetch_add(1, std::memory_order_relaxed);
  else if (!ram_phys(guest & 0x3fffffffu)) n_mmio.fetch_add(1, std::memory_order_relaxed);
  c->Rip += a.length;  // advance first: the slow path may longjmp and never return here
  slow_write(guest, a.width, value, a.fp);
  return EXCEPTION_CONTINUE_EXECUTION;
}

// ---- arena creation ----------------------------------------------------------------------------------------------------
void create_arena() {
  const char* page0 = std::getenv("NWII_ARENA_PAGE0");
  g_page0 = !(page0 && page0[0] == '0');
#if NWII_ARENA_MASK
  // One window, committed per bank by native_static_ram_commit.
  void* window = VirtualAlloc(reinterpret_cast<void*>(kBase), size_t(kSpan), MEM_RESERVE | MEM_WRITE_WATCH, PAGE_NOACCESS);
  if (window != reinterpret_cast<void*>(kBase)) die("cannot reserve the guest window", GetLastError());
  g_base_value = kBase;
#else
  HMODULE kernelbase = GetModuleHandleW(L"kernelbase.dll");
  auto valloc2 = kernelbase ? reinterpret_cast<PFN_VirtualAlloc2>(GetProcAddress(kernelbase, "VirtualAlloc2")) : nullptr;
  auto map3 = kernelbase ? reinterpret_cast<PFN_MapViewOfFile3>(GetProcAddress(kernelbase, "MapViewOfFile3")) : nullptr;
  if (!valloc2 || !map3) die("placeholder mappings need Windows 10 1803 or later", GetLastError());
  const HANDLE process = GetCurrentProcess();
#if NWII_ARENA_DYNAMIC_BASE
  void* requested = nullptr;
#else
  void* requested = reinterpret_cast<void*>(kBase);
#endif
  void* whole = nullptr;
#if NWII_ARENA_DYNAMIC_BASE
  {
    AddressRequirements above{reinterpret_cast<PVOID>(uintptr_t(1) << 32), nullptr, 0x10000};
    ExtendedParameter parameter{kAddressRequirementsType, &above};
    whole = valloc2(process, nullptr, size_t(kSpan), MEM_RESERVE | kReservePlaceholder, PAGE_NOACCESS, &parameter, 1);
  }
#endif
  if (!whole) whole = valloc2(process, requested, size_t(kSpan), MEM_RESERVE | kReservePlaceholder, PAGE_NOACCESS, nullptr, 0);
  if (!whole || (requested && whole != requested)) die("cannot reserve the 4 GiB guest arena", GetLastError());
  g_base_value = reinterpret_cast<uintptr_t>(whole);
  const uint64_t sizes[2] = {uint64_t(kMem1) + 0x10000u, uint64_t(kMem2) + 0x10000u};  // +64 KiB: the banks' 8-byte padding
  for (unsigned bank = 0; bank < 2; ++bank) {
    g_section[bank] = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, DWORD(sizes[bank] >> 32), DWORD(sizes[bank]), nullptr);
    if (!g_section[bank]) die("cannot create guest RAM section", GetLastError());
    // The views MMU::mem1/mem2 address directly (NativeFixedRam).
    void* window = MapViewOfFileEx(g_section[bank], FILE_MAP_WRITE, 0, 0, size_t(sizes[bank]), reinterpret_cast<void*>(window_address(bank, 0)));
    if (window != reinterpret_cast<void*>(window_address(bank, 0))) die("cannot place guest RAM at its fixed address", GetLastError());
    g_priv[bank] = static_cast<uint8_t*>(MapViewOfFile(g_section[bank], FILE_MAP_WRITE, 0, 0, size_t(sizes[bank])));
    if (!g_priv[bank]) die("cannot map the private guest RAM view", GetLastError());
  }
  for (unsigned q = 0; q < kQuadrants; ++q) {
    for (unsigned bank = 0; bank < 2; ++bank) {
      // Exactly the bank: the 8 padding bytes (24 MiB + 8, 64 MiB + 8) are visible to host code through the MMU window only,
      // so a guest access that reaches past the bank end faults and takes the MMU slow path, as it always did.
      const size_t bytes = bank ? kMem2 : kMem1;
      void* address = reinterpret_cast<void*>(view_address(q, bank, 0));
      if (!VirtualFree(address, bytes, MEM_RELEASE | kPreservePlaceholder)) die("cannot split the arena placeholder", GetLastError());
      void* view = map3(g_section[bank], process, address, 0, bytes, kReplacePlaceholder, PAGE_READWRITE, nullptr, 0);
      if (view != address) die("cannot map guest RAM into the arena", GetLastError());
    }
  }
#endif
  if (!AddVectoredExceptionHandler(1, arena_handler)) die("cannot install the guest memory fault handler", GetLastError());
  g_active = true;
  g_debug = std::getenv("NWII_ARENA_DEBUG") != nullptr;
  std::atexit(write_report);
  if (std::getenv("NWII_ARENA_SITES")) {   // refresh the report while the game runs: a killed or crashed process still leaves the counts
    std::thread([] { for (;;) { std::this_thread::sleep_for(std::chrono::seconds(5)); write_report(); } }).detach();
  }
  // On by default in the mirrored layouts: NWII_ARENA_TRACK=0 is the opt-out (the caches then compare bytes).
  const char* track = std::getenv("NWII_ARENA_TRACK");
  g_track_enabled = !NWII_ARENA_MASK && (!track || track[0] != '0');
  if (g_track_enabled) g_tracker = new Tracker();
}

void apply_sticky() {
  std::lock_guard<std::mutex> lock(g_sticky_mutex);
  // Release the previous policy.
  const unsigned old_n = g_sticky_count.load();
  for (unsigned i = 0; i < old_n; ++i) protect_sticky(g_sticky[i].phys_page << 12, PAGE_READWRITE);
  g_sticky_count.store(0, std::memory_order_release);
  unsigned n = 0;
  Sticky next[4];
  if (g_page0) next[n++] = {0u, kPage0};
  const uint32_t watch = g_watch_addr & 0x3fffffffu;
  if (g_watch_addr && ram_phys(watch)) {
    bool merged = false;
    for (unsigned i = 0; i < n; ++i) if (next[i].phys_page == (watch >> 12)) { next[i].kind |= kWatch; merged = true; }
    if (!merged) next[n++] = {watch >> 12, kWatch};
  }
  for (unsigned i = 0; i < n; ++i) { g_sticky[i] = next[i]; protect_sticky(next[i].phys_page << 12, PAGE_READONLY); }
  g_sticky_count.store(n, std::memory_order_release);
  // NATIVE_TEXTURE_VALIDATION_MEMO_V1: a changed policy can unprotect or protect tracked pages without a fault, so every
  // page epoch advances and no texture memo survives it.
  if (g_tracker) { const uint64_t stamp = next_write_epoch(); for (auto& e : g_tracker->page_epoch) e.store(stamp, std::memory_order_release); }
}

}  // namespace

void init() { std::call_once(g_once, [] { create_arena(); }); }
#if NWII_ARENA_DYNAMIC_BASE
const uintptr_t g_base = (init(), g_base_value);
#else
namespace { const bool g_boot = (init(), true); }  // reserve before drivers and other DLLs claim address space
#endif
bool active() { return g_active; }
void sync_watch() { if (g_active) apply_sticky(); }
void set_observer(Observer o) { g_observer = o; }
void report_sites(const char* path) { write_report_to(path); }
Stats stats() {
  Stats s;
  s.loads = n_loads; s.stores = n_stores; s.mmio = n_mmio; s.wgpipe = n_wg; s.sticky = n_sticky; s.tracker = n_tracker;
  s.unhandled = n_unhandled; s.sites = g_site_count;
  return s;
}

HostWriteScope::HostWriteScope(void* pointer, size_t count) : range(pointer), bytes(count), saved(0) {
  if (!g_active || !count) return;
  const uintptr_t first = reinterpret_cast<uintptr_t>(pointer), last = first + count - 1;
  // The kernel writes through `pointer`: make the sticky pages it covers writable in the view it names.
  const unsigned n = g_sticky_count.load(std::memory_order_acquire);
  for (unsigned i = 0; i < n; ++i) {
    for (unsigned v = 0; v < kViews; ++v) {
      const uintptr_t page = page_view(v, g_sticky[i].phys_page << 12);
      if (page <= last && page + 4096 > first) {
        DWORD old; VirtualProtect(reinterpret_cast<void*>(page), 4096, PAGE_READWRITE, &old);
        saved |= 1u << i;
      }
    }
  }
  // Only armed pages carry state (an unarmed page is always re-stamped by the next query), so the lock is taken only for those.
  if (g_tracker) {
    for (uintptr_t p = first & ~uintptr_t(0xfff); p <= last; p += 4096) {
      Region r;
      if (!classify(p, r) || !ram_phys(r.phys)) continue;
      if (!g_tracker->armed[tracker_index(r.phys)].load(std::memory_order_acquire)) continue;
      std::lock_guard<std::mutex> lock(g_track_mutex);
      const unsigned i = tracker_index(r.phys);
      if (g_tracker->armed[i].load()) { tracker_unprotect(r.phys); g_tracker->armed[i] = 0; g_tracker->dirty[i] = 1; g_tracker->page_epoch[i].store(next_write_epoch(), std::memory_order_release); }
    }
  }
}
HostWriteScope::~HostWriteScope() {
  if (!saved) return;
  for (unsigned i = 0; i < 4; ++i) if (saved & (1u << i)) protect_sticky(g_sticky[i].phys_page << 12, PAGE_READONLY);
}
}  // namespace native_arena

namespace nwii::runtime {
uint64_t native_ram_write_epoch() { return native_arena::g_write_epoch.load(std::memory_order_acquire); }
// MMU construction path of the NWII_STATIC_RAM_BASE build (replaces the VirtualAlloc based allocator).
void native_static_ram_commit(uintptr_t address, size_t bytes) {
  native_arena::init();
#if NWII_ARENA_MASK
  using namespace native_arena;
  if (address < kBase || bytes > kBase + kSpan - address ||
      !VirtualAlloc(reinterpret_cast<void*>(address), bytes, MEM_COMMIT, PAGE_READWRITE)) die("cannot commit guest RAM", GetLastError());
#else
  const bool bank1 = address == native_arena::kWindow1 && bytes <= native_arena::kMem1 + 8u;
  const bool bank2 = address == native_arena::kWindow2 && bytes <= native_arena::kMem2 + 8u;
  if (!bank1 && !bank2) {
    std::fprintf(stderr, "[Arena] unexpected RAM bank request 0x%llx/%llu\n", static_cast<unsigned long long>(address), static_cast<unsigned long long>(bytes));
    std::abort();
  }
#endif
  native_arena::sync_watch();
}

#if !NWII_ARENA_MASK
// Mirrored layouts only: section views cannot be MEM_WRITE_WATCH. The masked window keeps ram_write_watch.cpp.
uint64_t native_ram_generation() { return native_arena::g_generation.load(std::memory_order_acquire); }

uint64_t native_ram_newest_write(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes) {
  using namespace native_arena;
  if (!g_tracker || !bytes) return UINT64_MAX;
  int bank = -1;
  if (base == reinterpret_cast<const uint8_t*>(kWindow1)) bank = 0;
  else if (base == reinterpret_cast<const uint8_t*>(kWindow2)) bank = 1;
  if (bank < 0 || offset >= bank_bytes || bytes > bank_bytes - offset) return UINT64_MAX;
  const uint32_t first_phys = uint32_t(offset) + (bank ? 0x10000000u : 0u), last_phys = uint32_t(offset + bytes - 1) + (bank ? 0x10000000u : 0u);
  if (!ram_phys(first_phys) || !ram_phys(last_phys)) return UINT64_MAX;
  std::lock_guard<std::mutex> lock(g_track_mutex);
  Tracker& t = *g_tracker;
  if ((++t.queries & 1023u) == 0) for (auto& f : t.faults) f >>= 1;
  uint64_t newest = 0, stamp = 0;
  std::vector<uint32_t> to_arm;
  for (uint32_t phys = first_phys & ~0xfffu; phys <= last_phys; phys += 4096) {
    uint8_t sticky_kind;
    if (sticky_page(phys, &sticky_kind)) return UINT64_MAX;
    const unsigned i = tracker_index(phys);
    if (t.faults[i] >= 16) return UINT64_MAX;  // written constantly: let the caller compare bytes
    if (t.armed[i].load(std::memory_order_acquire) && !t.dirty[i].load(std::memory_order_acquire)) {
      if (t.generation[i] > newest) newest = t.generation[i];
      continue;
    }
    if (!stamp) stamp = g_generation.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (t.dirty[i].exchange(0)) { if (t.faults[i] < 255) ++t.faults[i]; }
    t.generation[i] = stamp;
    newest = stamp;
    to_arm.push_back(phys);
  }
  for (uint32_t phys : to_arm) {
    protect_page(phys, PAGE_READONLY);
    t.armed[tracker_index(phys)].store(1, std::memory_order_release);
  }
  g_pages_armed_total.fetch_add(to_arm.size(), std::memory_order_relaxed);
  return newest;
}
// NATIVE_TEXTURE_VALIDATION_MEMO_V1: largest page epoch of the tracked pages of [offset, offset+bytes) in the bank at base.
// Same range rules as native_ram_newest_write; UINT64_MAX when the arena tracker does not cover the range.
uint64_t native_ram_pages_epoch(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes) {
  using namespace native_arena;
  if (!g_tracker || !bytes) return UINT64_MAX;
  int bank = -1;
  if (base == reinterpret_cast<const uint8_t*>(kWindow1)) bank = 0;
  else if (base == reinterpret_cast<const uint8_t*>(kWindow2)) bank = 1;
  if (bank < 0 || offset >= bank_bytes || bytes > bank_bytes - offset) return UINT64_MAX;
  const uint32_t first_phys = uint32_t(offset) + (bank ? 0x10000000u : 0u), last_phys = uint32_t(offset + bytes - 1) + (bank ? 0x10000000u : 0u);
  if (!ram_phys(first_phys) || !ram_phys(last_phys)) return UINT64_MAX;
  uint64_t newest = 0;
  for (uint32_t phys = first_phys & ~0xfffu; phys <= last_phys; phys += 4096) {
    const uint64_t e = g_tracker->page_epoch[tracker_index(phys)].load(std::memory_order_acquire);
    if (e > newest) newest = e;
  }
  return newest;
}
uint64_t native_ram_pages_armed_total() { return native_arena::g_pages_armed_total.load(std::memory_order_relaxed); }
#else
// Masked layout: no arena tracker, so no page epochs (the texture memo never arms here).
uint64_t native_ram_pages_epoch(const uint8_t*, size_t, size_t, size_t) { return UINT64_MAX; }
uint64_t native_ram_pages_armed_total() { return 0; }
#endif  // !NWII_ARENA_MASK
}  // namespace nwii::runtime
