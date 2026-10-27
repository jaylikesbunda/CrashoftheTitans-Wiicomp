// Differential harness: a real generated hot unit is included as a translation unit and its functions run on randomised guest
// states. Built twice (previous accessors / arena accessors); the two executables print one line per trial and the outputs
// must be identical: CPUContext, RAM, MMIO / write-gather observations, and whether the run completed.
#include "stubs.h"
#if __has_include("runtime/native_guest_arena.h")
#include "runtime/native_guest_arena.h"
#define HAVE_ARENA 1
#endif
#include <random>
#include <memory>
#include <map>
#include <string>
#include <chrono>
using namespace nwii::runtime;

// ---- deterministic callees ----------------------------------------------------------------------------------------------
static uint64_t mix(uint64_t h, uint64_t v) { h ^= v + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2); return h * 0xff51afd7ed558ccdull; }
static uint64_t g_stub_hash = 0;
static unsigned g_polls = 0, g_poll_limit = 0;
static jmp_buf* g_abort = nullptr;
static void stub_callee(CPUContext& ctx, uint32_t id) {
  uint64_t h = mix(id, ctx.lr);
  for (int i = 3; i <= 10; ++i) h = mix(h, ctx.gpr[i]);
  g_stub_hash = mix(g_stub_hash, h);
  for (int i = 3; i <= 12; ++i) ctx.gpr[i] = uint32_t(mix(h, i) >> 11);
  ctx.gpr[0] = uint32_t(mix(h, 99) >> 7);
  for (int i = 0; i <= 5; ++i) { ctx.fpr[i] = double(float(int32_t(mix(h, 200 + i) >> 40)) * 0.25f); ctx.ps1[i] = ctx.fpr[i] + 1.0; }
  ctx.pc = ctx.lr;
  ++ctx.inst_count;
}
#include UNIT_PATH
#include "unit_stubs.inc"
namespace nwii::runtime {
NativeIndirectSite g_indirect_sites[4096];
void native_call_indirect_cached(CPUContext& ctx, uint32_t) { stub_callee(ctx, 0xCAFE); }
void native_call_indirect_due(CPUContext& ctx, uint32_t) { stub_callee(ctx, 0xCAFF); }
void native_call_indirect(CPUContext& ctx) { stub_callee(ctx, 0xCB00); }
bool process_pending_callbacks(CPUContext&) {
  if (++g_polls > g_poll_limit && g_abort) std::longjmp(*g_abort, 1);
  return false;
}
void note_null_call(uint32_t, uint32_t) {}
bool native_gx_emitter_ready() { return true; }
void native_dispatch_nested(CPUContext& ctx) { stub_callee(ctx, 0xCB01); }
}


static void fill_ram(MMU& m, uint64_t seed) {
  std::mt19937_64 rng(seed);
  // region of plausible data: floats of moderate magnitude, small integers and pointers, so that loaded values steer the code
  auto word = [&]() -> uint32_t {
    switch (rng() % 6) {
      case 0: { float f = float(int32_t(rng() % 20001) - 10000) / 64.f; uint32_t b; std::memcpy(&b, &f, 4); return __builtin_bswap32(b); }
      case 1: return __builtin_bswap32(0x80000000u | uint32_t(0x400000 + (rng() % 0x100000)));
      case 2: return __builtin_bswap32(uint32_t(rng() % 64));
      case 3: return __builtin_bswap32(0x90000000u | uint32_t(rng() % 0x1000000));
      default: { uint32_t b = uint32_t(rng()); if (((b >> 23) & 0xff) == 0xff) b &= ~(1u << 30);   // no NaN / Inf inputs (equivalent compiles may differ in NaN payload)
               return __builtin_bswap32(b); }
    }
  };
  for (uint32_t a = 0x00300000; a < 0x00800000; a += 4) { uint32_t w = word(); std::memcpy(m.mem1.data() + a, &w, 4); }
  for (uint32_t a = 0x00001000; a < 0x00002000; a += 4) { uint32_t w = word(); std::memcpy(m.mem1.data() + a, &w, 4); }
  for (uint32_t a = 0; a < 0x01000000; a += 4) { uint32_t w = word(); std::memcpy(m.mem2.data() + a, &w, 4); }
}
static uint64_t ram_hash(MMU& m) {
  uint64_t h = 1469598103934665603ull;
  auto go = [&](const uint8_t* p, size_t n) { for (size_t i = 0; i + 8 <= n; i += 8) { uint64_t v; std::memcpy(&v, p + i, 8); h = (h ^ v) * 0x100000001b3ull; h ^= h >> 29; } };
  go(m.mem1.data(), 24u << 20);
  go(m.mem2.data(), 64u << 20);
  return h;
}

static bool g_bench = false, g_bench_fifo = false;
static uint32_t pointer_like(std::mt19937_64& rng) {
  if (g_bench) {                       // RAM only (plus the write-gather base for FIFO writers): no faults, no loops over garbage
    const unsigned k = unsigned(rng() % 20);
    if (g_bench_fifo && k == 0) return 0xCC010000u;
    if (k < 12) return 0x80000000u | (0x300000u + uint32_t(rng() % 0x400000u)) & ~3u;
    if (k < 16) return 0x90000000u | (uint32_t(rng() % 0x1000000u) & ~3u);
    if (k < 18) return uint32_t(rng() % 64);
    return 0xC0000000u | (0x300000u + uint32_t(rng() % 0x400000u)) & ~3u;
  }
  switch (rng() % 20) {
    case 0: case 1: case 2: case 3: case 4: case 5: case 6: case 7: return 0x80000000u | (0x300000u + uint32_t(rng() % 0x400000u)) & ~3u;
    case 8: case 9: case 10: return 0x90000000u | (uint32_t(rng() % 0x1000000u) & ~3u);
    case 11: return 0xC0000000u | (0x300000u + uint32_t(rng() % 0x400000u)) & ~3u;
    case 12: return 16u + uint32_t(rng() % 240);   // small integers; physical words 0..15 (word 12 special case) are covered by the fault-parity test
    case 13: return uint32_t(-int32_t(rng() % 256));
    case 14: return uint32_t(rng());
    case 15: return 0xCC010000u;               // write-gather base with the -32768 displacement
    case 16: return 0xCC008000u;
    case 17: return 0xCC006800u + uint32_t(rng() % 64) * 4u;
    case 18: return 0x80000100u + (uint32_t(rng() % 0xf00u) & ~3u);     // low memory inside page 0, above the first 256 bytes (word 12: see the notes)
    default: return uint32_t(rng() % 1024) * 4u + 0x80300000u;
  }
}
static double fp_like(std::mt19937_64& rng) {
  switch (rng() % 8) {
    case 0: return 0.0;
    case 1: return 1.0;
    case 2: return -1.0;
    case 3: return double(float(int32_t(rng() % 2001) - 1000) / 16.f);
    case 4: return double(float(int32_t(rng())) );
    case 5: return double(float(double(rng() % 100000) * 1e-3));
    case 6: { uint32_t b = uint32_t(rng()); float f; std::memcpy(&f, &b, 4); return double(f); }
    default: return double(float(int32_t(rng() % 2001) - 1000) / 1024.f);
  }
}
static void randomize(CPUContext& ctx, std::mt19937_64& rng, uint32_t entry, uint32_t flavor) {
  for (int i = 0; i < 32; ++i) ctx.gpr[i] = pointer_like(rng);
  ctx.gpr[1] = 0x80700000u + (uint32_t(rng() % 0x10000u) & ~15u) + 0x1000u;
  ctx.gpr[2] = 0x80300000u + (uint32_t(rng() % 0x1000u) & ~3u);
  ctx.gpr[13] = 0x80380000u + (uint32_t(rng() % 0x1000u) & ~3u);
  if (flavor & 1) ctx.gpr[3] = 0x80400000u + (uint32_t(rng() % 0x1000u) & ~3u);
  for (int i = 0; i < 32; ++i) { ctx.fpr[i] = fp_like(rng); ctx.ps1[i] = fp_like(rng); }
  for (auto& f : ctx.cr) { f.lt = rng() & 1; f.gt = rng() & 1; f.eq = rng() & 1; f.so = rng() & 1; }
  ctx.lr = 0x80004000u + uint32_t(rng() % 0x1000) * 4u;
  ctx.ctr = uint32_t(rng() % 16);
  ctx.xer = uint32_t(rng()) & 0xE0000000u;
  ctx.msr = 0x2000u | 0x8000u;
  ctx.fpscr = 0;
  for (int i = 0; i < 8; ++i) {
    uint32_t g = 0;
    switch (rng() % 8) {
      case 0: case 1: case 2: case 3: g = 0; break;                                   // float pairs
      case 4: g = (4u << 16) | (uint32_t(rng() % 64) << 24) | 4u | (uint32_t(rng() % 64) << 8); break;
      case 5: g = (5u << 16) | (uint32_t(rng() % 64) << 24) | 5u | (uint32_t(rng() % 64) << 8); break;
      case 6: g = (6u << 16) | (uint32_t(rng() % 64) << 24) | 6u | (uint32_t(rng() % 64) << 8); break;
      default: g = (7u << 16) | (uint32_t(rng() % 64) << 24) | 7u | (uint32_t(rng() % 64) << 8); break;
    }
    ctx.gqr[i] = g;
  }
  ctx.srr0 = ctx.srr1 = 0;
  ctx.pc = entry;
  ctx.inst_count = 0;
  ctx.reservation_addr = 0xFFFFFFFFu;
  ctx.is_running = true;
  ctx.exception_pc = 0;
  ctx.in_callback = false;
}
static uint64_t ctx_hash(const CPUContext& c) {
  uint64_t h = 0x1234;
  for (uint32_t v : c.gpr) h = mix(h, v);
  // Any two NaNs compare equal: PowerPC returns frA's NaN where x86 returns the first source operand's, and GCC commutes
  // a+b / a*b freely, so equivalent compiles of one text may differ in NaN sign / payload only.
  auto bits = [](double v) { uint64_t b; std::memcpy(&b, &v, 8); return v != v ? 0x7ff8000000000000ull : b; };
  for (double v : c.fpr) h = mix(h, bits(v));
  for (double v : c.ps1) h = mix(h, bits(v));
  for (const auto& f : c.cr) h = mix(h, f.lt | (f.gt << 1) | (f.eq << 2) | (f.so << 3));
  h = mix(h, c.pc); h = mix(h, c.lr); h = mix(h, c.ctr); h = mix(h, c.xer); h = mix(h, c.msr); h = mix(h, c.fpscr);
  h = mix(h, c.inst_count); h = mix(h, c.srr0); h = mix(h, c.srr1); h = mix(h, c.reservation_addr);
  for (uint32_t v : c.gqr) h = mix(h, v);
  return h;
}
static uint64_t log_hash() {
  uint64_t h = 0x777;
  for (const Event& e : g_log) { h = mix(h, e.kind); h = mix(h, e.addr); h = mix(h, e.value); }
  return mix(h, g_log.size());
}

static __attribute__((noinline)) int run_one(NativeGuestFnPtr fn, CPUContext& ctx) {
  jmp_buf buf;
  g_abort = &buf;
  if (setjmp(buf) == 0) { fn(ctx); g_abort = nullptr; return 0; }
  g_abort = nullptr;
  return 1;
}

struct Target { const char* name; NativeGuestFnPtr fn; uint32_t entry; };


#include <x86intrin.h>
#include <windows.h>
// Warm microbenchmark: the same randomised states (RAM pointers only) run repeatedly; cycles per guest instruction of the best pass.
static int bench(CPUContext& ctx, MMU& m, const Target* targets, size_t count, unsigned states_wanted, unsigned passes) {
  SetThreadAffinityMask(GetCurrentThread(), 1ull << 2);
  SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS);
  SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
  g_bench = true;
  const bool fifo_run = std::getenv("BENCH_FIFO") != nullptr;
  for (size_t ti = 0; ti < count; ++ti) {
    const Target& t = targets[ti];
    g_bench_fifo = fifo_run;
    std::vector<unsigned> picked;
    fill_ram(m, 7);
    // pick states that complete (not aborted by the poll budget) and execute a meaningful number of instructions
    for (unsigned trial = 0; trial < 4000 && picked.size() < states_wanted; ++trial) {
      std::mt19937_64 rng(uint64_t(t.entry) * 1000003u + trial);
      randomize(ctx, rng, t.entry, trial);
      g_log.clear(); g_polls = 0; g_poll_limit = 400; g_native_poll_credit = 1 << 20;
      if (run_one(t.fn, ctx) == 0 && ctx.inst_count >= 150) picked.push_back(trial);
    }
    if (picked.empty()) { std::printf("%s: no completing states\n", t.name); continue; }
    uint64_t best = ~0ull, best_ic = 0;
    for (unsigned pass = 0; pass < passes + 2; ++pass) {
      uint64_t cycles = 0, ic = 0;
      for (unsigned trial : picked) {
        std::mt19937_64 rng(uint64_t(t.entry) * 1000003u + trial);
        randomize(ctx, rng, t.entry, trial);
        g_log.clear(); g_polls = 0; g_poll_limit = 400; g_native_poll_credit = 1 << 20;
          _mm_lfence();
        const uint64_t a = __rdtsc();
        const int status = run_one(t.fn, ctx);
        _mm_lfence();
        const uint64_t b = __rdtsc();
        if (status) continue;           // aborted by the poll budget (the evolving RAM changed the loop count): not comparable
        cycles += b - a; ic += ctx.inst_count;
      }
      if (pass >= 2 && cycles < best) { best = cycles; best_ic = ic; }
    }
    std::printf("%s: %zu states, %llu guest instructions, %llu cycles, %.3f cycles/guest-instruction\n", t.name, picked.size(),
                (unsigned long long)best_ic, (unsigned long long)best, double(best) / double(best_ic));
  }
#ifdef HAVE_ARENA
  { const auto st = native_arena::stats(); std::printf("arena faults during the run: %llu loads, %llu stores\n", (unsigned long long)st.loads, (unsigned long long)st.stores); }
#endif
  return 0;
}

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  const unsigned trials = argc > 1 ? unsigned(std::atoi(argv[1])) : 200;
  const unsigned ram_every = 20;
  auto ctx = std::make_unique<CPUContext>(); MMU& m = ctx->mmu; g_mmu = &m; g_ctx_ptr = ctx.get();
  g_native_poll_credit = 1 << 20;
  const Target targets[] = { TARGETS };
  g_watch_addr = 0;
  fill_ram(m, 99);
  uint64_t total_cycles = 0;
  if (argc > 2 && std::string(argv[2]) == "bench") return bench(*ctx, m, targets, sizeof targets / sizeof *targets, 24, 12);
  const char* only = std::getenv("ONLY");
  const unsigned from = std::getenv("FROM") ? unsigned(std::atoi(std::getenv("FROM"))) : 0u;
  for (const Target& t : targets) {
    if (only && std::string(only) != t.name) continue;
    unsigned aborted = 0, faults_before = 0;
    for (unsigned trial = from; trial < trials; ++trial) {
      std::mt19937_64 rng(uint64_t(t.entry) * 1000003u + trial);
      randomize(*ctx, rng, t.entry, trial);
      g_log.clear(); g_stub_hash = 0; g_polls = 0; g_poll_limit = 400;
      g_native_poll_credit = 64 + int(rng() % 64);
      static std::vector<uint8_t> before;
      const bool trace = std::getenv("TRACE") && unsigned(std::atoi(std::getenv("TRACE"))) == trial;
      if (trace) { before.assign(m.mem1.data(), m.mem1.data() + (24u << 20)); before.insert(before.end(), m.mem2.data(), m.mem2.data() + (64u << 20)); }
#ifdef MEMTRACE
      if (trace) { g_mt.clear(); g_mt_on = true; }
#endif
      const int status = run_one(t.fn, *ctx);
#ifdef MEMTRACE
      if (trace) { g_mt_on = false; for (const MemOp& o : g_mt) std::printf("  OP %c %u %08x %llx%c", o.k, o.w, o.a, (unsigned long long)o.v, 10); }
#endif
      if (trace) {
        size_t shown = 0;
        for (size_t i = 0; i + 4 <= before.size() && shown < 60; i += 4) {
          const uint8_t* now = i < (24u << 20) ? m.mem1.data() + i : m.mem2.data() + (i - (24u << 20));
          if (std::memcmp(&before[i], now, 4)) {
            const uint32_t phys = i < (24u << 20) ? uint32_t(i) : 0x10000000u + uint32_t(i - (24u << 20));
            uint32_t was, is; std::memcpy(&was, &before[i], 4); std::memcpy(&is, now, 4);
            std::printf("  RAM %08x: %08x -> %08x\n", phys, was, is); ++shown;
          }
        }
        size_t k = 0;
        for (const Event& e : g_log) { if (k++ < 40) std::printf("  EV %u %08x %016llx\n", e.kind, e.addr, (unsigned long long)e.value); }
      }
      aborted += status;
      std::printf("%s t=%u s=%d ctx=%016llx log=%016llx stubs=%016llx n=%zu ic=%llu", t.name, trial, status, (unsigned long long)ctx_hash(*ctx), (unsigned long long)log_hash(),
                  (unsigned long long)g_stub_hash, g_log.size(), (unsigned long long)ctx->inst_count);
      if (std::getenv("DUMP") && unsigned(std::atoi(std::getenv("DUMP"))) == trial) {
        std::printf(" [");
        for (int i = 0; i < 32; ++i) std::printf("r%d=%08x ", i, ctx->gpr[i]);
        for (int i = 0; i < 32; ++i) { uint64_t b; std::memcpy(&b, &ctx->fpr[i], 8); std::printf("f%d=%016llx ", i, (unsigned long long)b); }
        for (int i = 0; i < 32; ++i) { uint64_t b; std::memcpy(&b, &ctx->ps1[i], 8); std::printf("p%d=%016llx ", i, (unsigned long long)b); }
        std::printf("lr=%08x ctr=%08x xer=%08x pc=%08x]", ctx->lr, ctx->ctr, ctx->xer, ctx->pc);
      }
      if ((trial + 1) % ram_every == 0) std::printf(" ram=%016llx", (unsigned long long)ram_hash(m));
      std::printf("\n");
      if (trial % 10 == 9) fill_ram(m, 99 + trial);   // keep the data fresh without losing determinism
    }
    std::fprintf(stderr, "%s: %u trials, %u aborted by the poll budget\n", t.name, trials, aborted);
  }
#ifdef HAVE_ARENA
  {
    const auto st = native_arena::stats();
    std::fprintf(stderr, "arena faults: loads=%llu stores=%llu mmio=%llu wgpipe=%llu sticky=%llu unhandled=%llu sites=%llu\n", (unsigned long long)st.loads,
                 (unsigned long long)st.stores, (unsigned long long)st.mmio, (unsigned long long)st.wgpipe, (unsigned long long)st.sticky,
                 (unsigned long long)st.unhandled, (unsigned long long)st.sites);
  }
#endif
  return 0;
}
