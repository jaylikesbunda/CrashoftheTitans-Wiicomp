// Differential equivalence + timing harness for the whole-module LLVM IR.
//
// Compares the REAL generated C++ guest module against the emitted LLVM IR
// module on randomised CPUContext state, driving both with the SAME dispatcher:
//   * full guest register file  (memcmp over [0, offsetof(backup_stack)))
//   * inst_count, exception_pc
//   * raw guest RAM windows (MEM1 + MEM2)
//   * GX write-gather-pipe (FIFO) bytes
//
// usage: harness.exe [trials] [timeonly]
#include "runtime/cpu_context.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <random>
#include <vector>
#include <x86intrin.h>

using nwii::runtime::CPUContext;

// ---- runtime stubs the generated guest module needs -----------------------
namespace nwii::runtime {
bool g_trace_calls = false;
uint32_t g_watch_addr = 0;
int32_t g_native_poll_credit = 1;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext& c) { c.pc = 0; }
void watch_hit(uint32_t, uint32_t, int) {}
void note_null_call(uint32_t, uint32_t) {}
uint16_t HW_Reg_Read16(uint32_t a) { return uint16_t(a * 2654435761u); }
uint32_t HW_Reg_Read32(uint32_t a) { return a * 2654435761u; }
void HW_Reg_Write16(uint32_t, uint16_t) {}
void HW_Reg_Write32(uint32_t, uint32_t) {}
}  // namespace nwii::runtime

bool native_blocks_on = false;
void native_block_stub(CPUContext&) {}
namespace native_perf_census { void GuestSwitch(uint32_t, int) {} }

static std::vector<uint8_t> g_fifo;
namespace nwii::runtime {
void GX_WGPIPE_Write8(uint8_t v) { g_fifo.push_back(v); }
void GX_WGPIPE_Write16(uint16_t v) { g_fifo.push_back(uint8_t(v)); g_fifo.push_back(uint8_t(v >> 8)); }
void GX_WGPIPE_Write32(uint32_t v) { for (int i = 0; i < 4; ++i) g_fifo.push_back(uint8_t(v >> (8 * i))); }
}  // namespace nwii::runtime

extern "C" void nwii_interpret_step(CPUContext* c) { c->pc = 0; }
extern "C" uint8_t nwii_read8_slow(CPUContext* c, uint32_t a) { return c->mmu.native_read8_slow(a); }
extern "C" uint16_t nwii_read16_slow(CPUContext* c, uint32_t a) { return c->mmu.native_read16_slow(a); }
extern "C" uint32_t nwii_read32_slow(CPUContext* c, uint32_t a) { return c->mmu.native_read32_slow(a); }
extern "C" void nwii_write8_slow(CPUContext* c, uint32_t a, uint32_t v) { c->mmu.native_write8_slow(a, uint8_t(v)); }
extern "C" void nwii_write16_slow(CPUContext* c, uint32_t a, uint32_t v) { c->mmu.native_write16_slow(a, uint16_t(v)); }
extern "C" void nwii_write32_slow(CPUContext* c, uint32_t a, uint32_t v) { c->mmu.native_write32_slow(a, v); }
extern "C" void nwii_gx_wgpipe_write8(uint32_t v) { nwii::runtime::GX_WGPIPE_Write8(uint8_t(v)); }
extern "C" void nwii_gx_wgpipe_write16(uint32_t v) { nwii::runtime::GX_WGPIPE_Write16(uint16_t(v)); }
extern "C" void nwii_gx_wgpipe_write32(uint32_t v) { nwii::runtime::GX_WGPIPE_Write32(v); }
extern "C" void nwii_ir_interpret_step(CPUContext* c) { c->pc = 0; }

#include "module_data.inc"

static constexpr size_t kGuestBytes = 760;
static constexpr size_t kMemWindow = 1u << 20;
static constexpr int kStepCap = 96;

static void drive_cpp(CPUContext& c) {
  for (int i = 0; i < kStepCap; ++i) {
    switch (c.pc) {
#define CASE(addr) case 0x##addr##u: ref::func_##addr(c); break;
      MODULE_CASES(CASE)
#undef CASE
      default: return;
    }
    if (c.pc == 0) return;
  }
}

static void drive_ir(CPUContext& c) {
  for (int i = 0; i < kStepCap; ++i) {
    switch (c.pc) {
#define CASE(addr) case 0x##addr##u: nwii_entry_##addr(&c); break;
      MODULE_CASES(CASE)
#undef CASE
      default: return;
    }
    if (c.pc == 0) return;
  }
}

static void setup(CPUContext& c, std::mt19937& q, uint32_t ram_seed, uint32_t start) {
  for (int i = 0; i < 32; ++i) {
    uint32_t k = q() % 4, v = q();
    c.gpr[i] = k == 0 ? 0x80100000u + ((v & 0x7FFu) << 2)
             : k == 1 ? (v & 0xFFu)
             : k == 2 ? (0x80100000u + ((v & 0x3FFu) << 2))
                      : (0x80000000u + (v & 0x3FFCu));
  }
  c.gpr[1] = 0x80100000u + ((q() & 0x3FFu) << 2);
  c.gpr[2] = 0x80110000u + ((q() & 0xFFu) << 2);
  c.gpr[13] = 0x80120000u + (q() & 0x3FFu);
  for (int i = 0; i < 32; ++i) { c.fpr[i] = 0; c.ps1[i] = 0; }
  for (int i = 0; i < 8; ++i) { uint32_t v = q(); c.cr[i].lt = v & 1; c.cr[i].gt = v & 2; c.cr[i].eq = v & 4; c.cr[i].so = v & 8; }
  c.pc = start;
  c.lr = 0x80000F00u | (q() & 0xFFu);
  c.ctr = (q() & 0x3Fu) + 1;
  c.xer = q() & 0xE000007Fu;
  c.msr = 0x2000u;
  c.fpscr = 0;
  c.srr0 = q(); c.srr1 = q();
  for (int i = 0; i < 8; ++i) c.gqr[i] = 0;
  for (int i = 0; i < 4; ++i) c.sprg[i] = 0;
  c.inst_count = q() & 0xFFu;
  c.exception_pc = 0;
  std::mt19937 r(ram_seed);
  for (size_t i = 0; i < kMemWindow; i += 4) {
    uint32_t v = r();
    memcpy(c.mmu.mem1.data() + i, &v, 4);
    memcpy(c.mmu.mem2.data() + i, &v, 4);
  }
}

// root list (generated)
static const uint32_t kRoots[] = { MODULE_ROOTS };

static bool run_case(uint32_t root, uint32_t seed, int* detail) {
  auto A = std::make_unique<CPUContext>(), B = std::make_unique<CPUContext>();
  std::mt19937 qa(seed), qb(seed);
  setup(*A, qa, qa(), root);
  setup(*B, qb, qb(), root);
  g_fifo.clear(); drive_cpp(*A); std::vector<uint8_t> fa = g_fifo;
  g_fifo.clear(); drive_ir(*B); std::vector<uint8_t> fb = g_fifo;
  if (memcmp(A.get(), B.get(), kGuestBytes)) { *detail = 1; return false; }
  if (A->inst_count != B->inst_count) { *detail = 2; return false; }
  if (A->exception_pc != B->exception_pc) { *detail = 3; return false; }
  if (memcmp(A->mmu.mem1.data(), B->mmu.mem1.data(), kMemWindow)) { *detail = 4; return false; }
  if (memcmp(A->mmu.mem2.data(), B->mmu.mem2.data(), kMemWindow)) { *detail = 5; return false; }
  if (fa != fb) { *detail = 6; return false; }
  return true;
}

static uint64_t time_drive(CPUContext& c, void (*fn)(CPUContext&), int iters, uint32_t root) {
  uint64_t best = ~0ull;
  for (int r = 0; r < 24; ++r) {
    _mm_lfence();
    uint64_t t0 = __rdtsc();
    _mm_lfence();
    for (int i = 0; i < iters; ++i) {
      c.pc = root; c.inst_count = 1;
      fn(c);
    }
    _mm_lfence();
    uint64_t t1 = __rdtsc();
    _mm_lfence();
    best = std::min(best, t1 - t0);
  }
  return best / iters;
}

int main(int argc, char** argv) {
  int trials = argc > 1 ? atoi(argv[1]) : 3000;
  bool timeonly = argc > 2 && strcmp(argv[2], "timeonly") == 0;
  if (!timeonly) {
    int bad = 0, total = 0;
    for (unsigned ri = 0; ri < sizeof(kRoots) / sizeof(kRoots[0]); ++ri)
      for (int t = 0; t < trials; ++t) {
        int detail = 0;
        ++total;
        if (!run_case(kRoots[ri], 0x1234567u + t * 2654435761u + ri * 7919u, &detail)) {
          if (bad < 12) printf("MISMATCH root=0x%08X seed=%d detail=%d\n", kRoots[ri], t, detail);
          ++bad;
        }
      }
    printf("EQUIV roots=%u trials_per_root=%d total=%d mismatches=%d\n",
           (unsigned)(sizeof(kRoots) / sizeof(kRoots[0])), trials, total, bad);
    if (bad) return 1;
  }
  auto C = std::make_unique<CPUContext>();
  std::mt19937 q(999);
  setup(*C, q, q(), kRoots[0]);
  volatile uint32_t sink = 0;
  const int iters = 30000;
  for (unsigned ri = 0; ri < sizeof(kRoots) / sizeof(kRoots[0]); ++ri) {
    uint32_t root = kRoots[ri];
    // warmup
    for (int i = 0; i < 4000; ++i) { C->pc = root; C->inst_count = 1; drive_cpp(*C); }
    uint64_t best_cpp = ~0ull, best_ir = ~0ull;
    for (int r = 0; r < 6; ++r) {
      best_cpp = std::min(best_cpp, time_drive(*C, drive_cpp, iters, root));
      best_ir = std::min(best_ir, time_drive(*C, drive_ir, iters, root));
    }
    sink = C->gpr[3];
    printf("TIME root=0x%08X cycles_per_drive cpp=%llu ir=%llu\n", root,
           (unsigned long long)best_cpp, (unsigned long long)best_ir);
  }
  printf("sink=%u\n", sink);
  return 0;
}
