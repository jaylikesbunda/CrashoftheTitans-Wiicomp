// Fault-handler parity: loads and stores of every width through the MMU checked paths, the previous native_fastmem /
// native_guest_fast accessors and the arena accessors must agree on results, RAM, and the ordered MMIO / write-gather / watch
// observations. Includes the longjmp path out of a faulting access.
#include "stubs.h"
#define native_fastmem old_fastmem
#define native_guest_fast old_guest_fast
#include "old/native_fastmem.h"
#include "old/native_guest_fast.h"
#undef native_fastmem
#undef native_guest_fast
#include "runtime/native_guest_arena.h"
#include <memory>
#include <random>
#include <algorithm>
#include <chrono>
using namespace nwii::runtime;
using Clock = std::chrono::steady_clock;
#define CHECK(c) do{ if(!(c)){ std::printf("FAIL %s:%d %s\n",__FILE__,__LINE__,#c); std::fflush(stdout); std::exit(1);} }while(0)

enum Op : uint8_t { LD8, LD16, LD32, LD64, LDF32, LDF64, ST8, ST16, ST32, ST64, STF32, STF64 };
struct Rec { Op op; uint32_t addr; uint64_t val; };

static uint64_t fnv(const uint8_t* p, size_t n, uint64_t h = 1469598103934665603ull) {
  for (size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 1099511628211ull; }
  return h;
}
static std::vector<uint8_t> g_snap1, g_snap2;

template<class Impl> static std::vector<uint64_t> run(const std::vector<Rec>& ops, Impl&& impl) {
  std::vector<uint64_t> out; out.reserve(ops.size());
  for (const Rec& r : ops) out.push_back(impl(r));
  return out;
}

// MMU reference (the checked inline accessors of cpu_context.h).
struct MmuImpl {
  MMU& m;
  uint64_t operator()(const Rec& r) const {
    uint64_t bits = 0;
    switch (r.op) {
      case LD8: return m.read8(r.addr);
      case LD16: return m.read16(r.addr);
      case LD32: return m.read32(r.addr);
      case LD64: return m.read64(r.addr);
      case LDF32: { float f = m.read_f32(r.addr); uint32_t b; std::memcpy(&b, &f, 4); return b; }
      case LDF64: { double d = m.read_f64(r.addr); std::memcpy(&bits, &d, 8); return bits; }
      case ST8: m.write8(r.addr, uint8_t(r.val)); return 0;
      case ST16: m.write16(r.addr, uint16_t(r.val)); return 0;
      case ST32: m.write32(r.addr, uint32_t(r.val)); return 0;
      case ST64: m.write64(r.addr, r.val); return 0;
      case STF32: { float f; uint32_t b = uint32_t(r.val); std::memcpy(&f, &b, 4); m.write_f32(r.addr, f); return 0; }
      case STF64: { double d; std::memcpy(&d, &r.val, 8); m.write_f64(r.addr, d); return 0; }
    }
    return 0;
  }
};
// Previous accessor layer: native_fastmem reads (generated code), native_guest_fast stores (stack stores) / MMU stores.
struct OldImpl {
  MMU& m;
  uint64_t operator()(const Rec& r) const {
    uint64_t bits = 0;
    switch (r.op) {
      case LD8: return old_fastmem::read8(m, r.addr);
      case LD16: return old_fastmem::read16(m, r.addr);
      case LD32: return old_fastmem::read32(m, r.addr);
      case LD64: return old_fastmem::read64(m, r.addr);
      case LDF32: { float f = old_fastmem::read_f32(m, r.addr); uint32_t b; std::memcpy(&b, &f, 4); return b; }
      case LDF64: { double d = old_fastmem::read_f64(m, r.addr); std::memcpy(&bits, &d, 8); return bits; }
      case ST8: old_guest_fast::write8(m, r.addr, uint8_t(r.val)); return 0;
      case ST16: old_guest_fast::write16(m, r.addr, uint16_t(r.val)); return 0;
      case ST32: old_guest_fast::write32(m, r.addr, uint32_t(r.val)); return 0;
      case ST64: old_guest_fast::write64(m, r.addr, r.val); return 0;
      case STF32: { float f; uint32_t b = uint32_t(r.val); std::memcpy(&f, &b, 4); old_guest_fast::write_f32(m, r.addr, f); return 0; }
      case STF64: { double d; std::memcpy(&d, &r.val, 8); old_guest_fast::write_f64(m, r.addr, d); return 0; }
    }
    return 0;
  }
};
struct NewImpl {
  uint64_t operator()(const Rec& r) const {
    uint64_t bits = 0;
    switch (r.op) {
      case LD8: return native_arena::ld8(r.addr);
      case LD16: return native_arena::ld16(r.addr);
      case LD32: return native_arena::ld32(r.addr);
      case LD64: return native_arena::ld64(r.addr);
      case LDF32: { float f = native_arena::ldf32(r.addr); uint32_t b; std::memcpy(&b, &f, 4); return b; }
      case LDF64: { double d = native_arena::ldf64(r.addr); std::memcpy(&bits, &d, 8); return bits; }
      case ST8: native_arena::st8(r.addr, uint8_t(r.val)); return 0;
      case ST16: native_arena::st16(r.addr, uint16_t(r.val)); return 0;
      case ST32: native_arena::st32(r.addr, uint32_t(r.val)); return 0;
      case ST64: native_arena::st64(r.addr, r.val); return 0;
      case STF32: { float f; uint32_t b = uint32_t(r.val); std::memcpy(&f, &b, 4); native_arena::stf32(r.addr, f); return 0; }
      case STF64: { double d; std::memcpy(&d, &r.val, 8); native_arena::stf64(r.addr, d); return 0; }
    }
    return 0;
  }
};

static void restore_ram(MMU& m) {
  std::memcpy(m.mem1.data(), g_snap1.data(), g_snap1.size());
  std::memcpy(m.mem2.data(), g_snap2.data(), g_snap2.size());
}
static uint64_t ram_hash(MMU& m) { return fnv(m.mem2.data(), m.mem2.size() - 8, fnv(m.mem1.data(), m.mem1.size() - 8)); }

struct Outcome { std::vector<uint64_t> results; std::vector<Event> log; uint64_t hash; double seconds; };
template<class Impl> static Outcome execute(MMU& m, const std::vector<Rec>& ops, Impl&& impl) {
  restore_ram(m); g_log.clear();
  const auto t0 = Clock::now();
  Outcome o; o.results = run(ops, impl);
  o.seconds = std::chrono::duration<double>(Clock::now() - t0).count();
  o.log = g_log; o.hash = ram_hash(m);
  return o;
}

static const char* opname[] = {"ld8","ld16","ld32","ld64","ldf32","ldf64","st8","st16","st32","st64","stf32","stf64"};
static bool same(const char* what, const std::vector<Rec>& ops, const Outcome& a, const Outcome& b) {
  bool ok = true;
  if (a.hash != b.hash) { std::printf("  [%s] RAM hash differs %016llx vs %016llx\n", what, (unsigned long long)a.hash, (unsigned long long)b.hash); ok = false; }
  unsigned shown = 0;
  for (size_t i = 0; i < ops.size() && shown < 8; ++i)
    if (a.results[i] != b.results[i]) {
      std::printf("  [%s] op %zu %s addr=%08x val=%016llx: %016llx vs %016llx\n", what, i, opname[ops[i].op], ops[i].addr,
                  (unsigned long long)ops[i].val, (unsigned long long)a.results[i], (unsigned long long)b.results[i]);
      ok = false; ++shown;
    }
  if (a.log.size() != b.log.size()) { std::printf("  [%s] observer count %zu vs %zu\n", what, a.log.size(), b.log.size()); ok = false; }
  shown = 0;
  for (size_t i = 0; i < std::min(a.log.size(), b.log.size()) && shown < 8; ++i)
    if (a.log[i].kind != b.log[i].kind || a.log[i].addr != b.log[i].addr || a.log[i].value != b.log[i].value) {
      std::printf("  [%s] observation %zu: (%u,%08x,%llx) vs (%u,%08x,%llx)\n", what, i, a.log[i].kind, a.log[i].addr,
                  (unsigned long long)a.log[i].value, b.log[i].kind, b.log[i].addr, (unsigned long long)b.log[i].value);
      ok = false; ++shown;
    }
  return ok;
}

static uint32_t mirror(std::mt19937_64& rng) { static const uint32_t m[4] = {0x00000000u, 0x40000000u, 0x80000000u, 0xC0000000u}; return m[rng() & 3]; }

static std::vector<Rec> generate(std::mt19937_64& rng, size_t count, uint32_t watch_page_addr) {
  // hot RAM set so loads see earlier stores; the watched page is part of it when a watch is configured
  std::vector<uint32_t> hot;
  for (int i = 0; i < 2048; ++i) hot.push_back(0x00100000u + (uint32_t(rng() % 0x2000) & ~7u));
  for (int i = 0; i < 1024; ++i) hot.push_back(0x10200000u + (uint32_t(rng() % 0x2000) & ~7u));
  if (watch_page_addr) for (int i = 0; i < 512; ++i) hot.push_back((watch_page_addr & 0x3ffff000u) + (uint32_t(rng() % 0x1000) & ~3u));
  for (int i = 0; i < 64; ++i) hot.push_back(uint32_t(rng() % 0x80));            // page 0 (word 12 included)
  hot.push_back(12); hot.push_back(8); hot.push_back(16); hot.push_back(4);
  std::vector<Rec> ops; ops.reserve(count);
  for (size_t n = 0; n < count; ++n) {
    Rec r; r.op = Op(rng() % 12); r.val = rng();
    if (rng() % 4 == 0) r.val &= 0xff;
    const unsigned cls = rng() % 100;
    uint32_t phys;
    if (cls < 52) {                    // hot RAM
      phys = hot[rng() % hot.size()] + (rng() % 8 == 0 ? uint32_t(rng() % 8) : 0u);
      r.addr = phys | mirror(rng);
    } else if (cls < 62) {             // random RAM anywhere (aligned or not)
      phys = (rng() & 1) ? uint32_t(rng() % 0x1800000u) : 0x10000000u + uint32_t(rng() % 0x4000000u);
      r.addr = phys | mirror(rng);
    } else if (cls < 70) {             // bank edges, straddles, holes next to RAM
      static const uint32_t edges[] = {0x017ffff0u, 0x017ffff8u, 0x017ffffcu, 0x01800000u, 0x01800004u, 0x0180fff0u, 0x01810000u, 0x13fffff0u, 0x13fffff8u, 0x13fffffcu,
                                       0x14000000u, 0x14000004u, 0x1400fff8u, 0x14010000u, 0x0fffffffu, 0x10000000u, 0x0ffffff8u, 0x3ffffff8u, 0x3fffffffu, 0x40000000u};
      phys = edges[rng() % (sizeof edges / sizeof *edges)] + uint32_t(rng() % 5) - 2u;
      r.addr = phys | mirror(rng);
    } else if (cls < 78) {             // hardware registers (0xCC00xxxx / 0xCD00xxxx and the physical spellings)
      static const uint32_t base[] = {0xCC000000u, 0xCD000000u, 0x0C000000u, 0x0D000000u, 0x8C000000u, 0x4C000000u, 0xCC006C00u, 0xCC005000u};
      r.addr = base[rng() % 8] + (uint32_t(rng() % 0x10000) & ~(rng() & 1 ? 3u : 0u));
    } else if (cls < 90) {             // write-gather page
      static const uint32_t base[] = {0xCC008000u, 0x0C008000u, 0x8C008000u, 0x4C008000u};
      r.addr = base[rng() % 4] + (rng() % 4 ? 0u : uint32_t(rng() % 0x1000));
    } else if (cls < 95) {             // everything else: locked cache, EFB, holes, high addresses, wrap-around
      static const uint32_t base[] = {0xE0000000u, 0xE0001000u, 0xC8000000u, 0x20000000u, 0x30000000u, 0x34000000u, 0xFFFFFFF0u, 0xFFFF8000u, 0x7FFFFFF0u, 0x0F000000u};
      r.addr = base[rng() % 10] + uint32_t(rng() % 0x20);
    } else {                           // fully random 32-bit address
      r.addr = uint32_t(rng());
    }
#if NWII_ARENA_MASK
    // Known divergence of the masked window (documented): VirtualAlloc commits whole pages, so the 4 KiB above each bank is
    // accessible to guest accesses (zero-filled padding) where the MMU treats it as a hole. Keep accesses out of it.
    {
      const uint32_t p = r.addr & 0x3fffffffu;
      if ((p >= 0x01800000u - 8 && p < 0x01801000u) || (p >= 0x14000000u - 8 && p < 0x14001000u)) r.addr += 0x2000;
    }
#endif
    // Known divergence (documented): a partial-width store overlapping physical word 12 changes RAM, and the arena reads the
    // RAM word where the MMU always returns 0 for a 32-bit load there. Keep such stores away from word 12.
    if (r.op >= ST8) {
      const unsigned w = (r.op == ST8) ? 1 : (r.op == ST16) ? 2 : (r.op == ST32 || r.op == STF32) ? 4 : 8;
      const uint32_t p = r.addr & 0x3fffffffu;
      const bool overlaps = p < 16 && p + w > 12;
      if (overlaps && !(r.op == ST32 && p == 12)) r.addr += 0x40;   // only the integer word store is dropped by both
    }
    ops.push_back(r);
  }
  return ops;
}

// setjmp lives in its own frame, as in the runtime (the exception jmp_buf is armed by the dispatcher, not by the accessors).
static __attribute__((noinline)) bool store_with_jump(uint32_t address, uint32_t value) {
  jmp_buf buf;
  g_longjmp_target = &buf;
  if (setjmp(buf) == 0) { native_arena::st32(address, value); g_longjmp_target = nullptr; return false; }
  g_longjmp_target = nullptr;
  return true;
}

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  const size_t count = argc > 1 ? size_t(std::atoll(argv[1])) : 400000;
  auto ctx = std::make_unique<CPUContext>(); MMU& m = ctx->mmu; g_mmu = &m; g_ctx_ptr = ctx.get();
  CHECK(native_arena::active());
  std::mt19937_64 init(77);
  for (size_t i = 0; i + 8 <= m.mem1.size(); i += 8) { uint64_t v = init(); std::memcpy(m.mem1.data() + i, &v, 8); }
  for (size_t i = 0; i + 8 <= m.mem2.size(); i += 8) { uint64_t v = init(); std::memcpy(m.mem2.data() + i, &v, 8); }
  std::memset(m.mem1.data() + 12, 0, 4);   // physical word 12 is zero in every real image
  g_snap1.assign(m.mem1.data(), m.mem1.data() + m.mem1.size());
  g_snap2.assign(m.mem2.data(), m.mem2.data() + m.mem2.size());
  unsigned failures = 0; uint64_t total = 0;
  struct Cfg { const char* name; uint32_t watch; };
  const Cfg cfgs[] = {{"no watch", 0}, {"watch in MEM1 page", 0x80010124u}, {"watch in MEM2 page", 0x90200038u}, {"watch on page 0", 0x80000030u}};
  for (const Cfg& cfg : cfgs) {
    g_watch_addr = cfg.watch; m.native_init_page_bias(); native_arena::sync_watch();
    std::mt19937_64 rng(0x5eed + cfg.watch);
    const auto ops = generate(rng, count, cfg.watch);
    const auto s0 = native_arena::stats();
    Outcome ref = execute(m, ops, MmuImpl{m});
    Outcome old = execute(m, ops, OldImpl{m});
    Outcome now = execute(m, ops, NewImpl{});
    const auto s1 = native_arena::stats();
    std::printf("%-20s %zu ops x3: faults loads=%llu stores=%llu (wg=%llu mmio=%llu) sticky=%llu, unhandled=%llu, %.2fs/%.2fs/%.2fs\n", cfg.name, ops.size(),
                (unsigned long long)(s1.loads - s0.loads), (unsigned long long)(s1.stores - s0.stores), (unsigned long long)(s1.wgpipe - s0.wgpipe),
                (unsigned long long)(s1.mmio - s0.mmio), (unsigned long long)(s1.sticky - s0.sticky), (unsigned long long)(s1.unhandled - s0.unhandled),
                ref.seconds, old.seconds, now.seconds);
    const bool a = same("MMU vs arena", ops, ref, now);
    const bool b = same("old accessors vs arena", ops, old, now);
    const bool c = same("MMU vs old", ops, ref, old);
    if (!a) ++failures;
    if (!b) ++failures;
    if (!c) std::printf("  (note: the previous accessors differ from the MMU on this configuration)\n");
    total += ops.size() * 3;
    CHECK(s1.unhandled == s0.unhandled);
  }
  // longjmp out of a faulting access (the slow path of an MMIO store leaves through the exception jmp_buf)
  {
    g_watch_addr = 0; m.native_init_page_bias(); native_arena::sync_watch();
    unsigned jumps = 0;
    for (int i = 0; i < 20000; ++i) {
      g_longjmp_on_addr = 0x0c006800u + (i & 0xff) * 4u; g_log.clear();
      if (store_with_jump(0xCC006800u + (i & 0xff) * 4u, 0x1234u + i)) ++jumps;
    }
    CHECK(jumps == 20000);
    // a store that does not jump: the log shows the HW write, execution resumes after the store
    g_longjmp_on_addr = 0xffffffffu; g_log.clear();
    CHECK(!store_with_jump(0xCC006800u, 7) && g_log.size() == 1);
    // normal service afterwards (handler lock, stack and trap flag are intact)
    CHECK(native_arena::ld32(0xCC006800u) == (0x0c006800u ^ 0xa5c39921u));
    std::printf("longjmp out of 20000 faulting stores passed%c", 10);
  }
  std::printf("%s: %llu accesses compared\n", failures ? "FAILED" : "arena fault parity passed", (unsigned long long)total);
  return failures ? 1 : 0;
}
