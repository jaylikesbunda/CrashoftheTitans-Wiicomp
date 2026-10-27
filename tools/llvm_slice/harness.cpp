// Differential equivalence + timing harness for the LLVM-IR slice.
//
// Compares the REAL generated C++ guest function against the hand-emitted LLVM
// IR on randomised CPUContext state:
//   * full guest register file  (memcmp over [0, offsetof(backup_stack)))
//   * inst_count, exception_pc
//   * raw guest RAM windows (MEM1 + MEM2)
//   * GX write-gather-pipe (FIFO) bytes
//
// usage: harness.exe [trials]
#include "runtime/cpu_context.h"
#include <algorithm>
#include <bit>
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

namespace nwii::runtime {
bool g_trace_calls = false;
uint32_t g_watch_addr = 0;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext& c) { c.pc = 0; }
void watch_hit(uint32_t, uint32_t, int) {}
uint16_t HW_Reg_Read16(uint32_t a) { return uint16_t(a * 2654435761u); }
uint32_t HW_Reg_Read32(uint32_t a) { return a * 2654435761u; }
void HW_Reg_Write16(uint32_t, uint16_t) {}
void HW_Reg_Write32(uint32_t, uint32_t) {}
}  // namespace nwii::runtime

// The IR slice can be linked either as a distinct extern "C" symbol (the spike
// naming) or, exactly as the hybrid backend does, as the real linker name of
// the generated function.  functions.h declares every guest function with C
// linkage, so the object provides the plain `func_XXXXXXXX` symbol:
//   -DNWII_IR_C_NAME=func_XXXXXXXX   (C linkage, the production convention)
//   -DNWII_IR_CPP_NAME=func_XXXXXXXX (C++ linkage, the spike's original probe)
#ifdef NWII_IR_CPP_NAME
void NWII_IR_CPP_NAME(::nwii::runtime::CPUContext&);
#elif defined(NWII_IR_C_NAME)
extern "C" void NWII_IR_C_NAME(::nwii::runtime::CPUContext&);
#endif

static std::vector<uint8_t> g_fifo;
namespace nwii::runtime {
void GX_WGPIPE_Write8(uint8_t v) { g_fifo.push_back(v); }
void GX_WGPIPE_Write16(uint16_t v) { g_fifo.push_back(uint8_t(v)); g_fifo.push_back(uint8_t(v >> 8)); }
void GX_WGPIPE_Write32(uint32_t v) { for (int i = 0; i < 4; ++i) g_fifo.push_back(uint8_t(v >> (8 * i))); }
}  // namespace nwii::runtime

extern "C" void nwii_ir_interpret_step(CPUContext* c) { nwii::runtime::interpret_step(*c); }
extern "C" uint32_t nwii_ir_read32_slow(CPUContext* c, uint32_t a) { return c->mmu.native_read32_slow(a); }
extern "C" void nwii_ir_write32_slow(CPUContext* c, uint32_t a, uint32_t v) { c->mmu.native_write32_slow(a, v); }
extern "C" void nwii_ir_gx_wgpipe_write32(uint32_t v) { nwii::runtime::GX_WGPIPE_Write32(v); }

#ifndef NWII_REF_FUNC
#define NWII_REF_FUNC func_8023B080
#endif
#ifndef NWII_IR_ENTRY
#define NWII_IR_ENTRY 0x8023B080u
#endif
namespace ref_o2 { void NWII_REF_FUNC(CPUContext&); }
namespace ref_os { void NWII_REF_FUNC(CPUContext&); }
#ifdef HAVE_IR
#ifndef NWII_IR_CPP_NAME
extern "C" void func_ir_o2(CPUContext&);
extern "C" void func_ir_os(CPUContext&);
#endif
#endif

static constexpr uint32_t kEntry = NWII_IR_ENTRY;
static constexpr size_t kGuestBytes = 760;
static constexpr size_t kMemWindow = 1u << 20;

static uint32_t pick_addr(std::mt19937& q, unsigned regime) {
  switch (regime) {
    case 0: return (q() & 0xFFFFFu) & ~3u;
    case 1: return 0x10000000u + ((q() & 0xFFFFFu) & ~3u);
    case 2: return 0xCu;
    case 3: return (q() & 0xFFFCu);
    case 4: return 0x0C008000u + ((q() & 0x3Fu) & ~3u);
    case 5: return 0x0C000000u + (q() & 0x3FFFCu);
    case 6: return q() & 0x3FFFFFFFu;
    default: return (q() & 0x3FFFu) & ~3u;
  }
}

static void setup(CPUContext& c, std::mt19937& q, unsigned regime, uint32_t ram_seed) {
  for (int i = 0; i < 32; ++i) {
    uint32_t k = q() % 4, v = q();
    c.gpr[i] = k == 0 ? 0x80100000u + ((v & 0x7FFu) << 2)
             : k == 1 ? (v & 0xFFu)
             : k == 2 ? (0x80100000u + ((v & 0x3FFu) << 2))
                      : (0x80000000u + (v & 0x3FFCu));
  }
  c.gpr[3] = (q() & 1) ? 0u : (q() | 1u);
  c.gpr[13] = pick_addr(q, regime) + 30488u;
  for (int i = 0; i < 32; ++i) { c.fpr[i] = double(int32_t(q())) / 4096.0; c.ps1[i] = double(int32_t(q())) / 4096.0; }
  for (int i = 0; i < 8; ++i) { uint32_t v = q(); c.cr[i].lt = v & 1; c.cr[i].gt = v & 2; c.cr[i].eq = v & 4; c.cr[i].so = v & 8; }
  c.pc = kEntry;
  c.lr = 0x80000F00u | (q() & 0xFFu);
  c.ctr = (q() & 0x3Fu) + 1;
  c.xer = q() & 0xE000007Fu;
  c.msr = 0x2000u;
  c.fpscr = q();
  c.srr0 = q(); c.srr1 = q();
  for (int i = 0; i < 8; ++i) c.gqr[i] = q();
  for (int i = 0; i < 4; ++i) c.sprg[i] = q();
  c.inst_count = q() & 0xFFu;
  c.exception_pc = q();
  std::mt19937 r(ram_seed);
  for (size_t i = 0; i < kMemWindow; i += 4) {
    uint32_t v = r();
    memcpy(c.mmu.mem1.data() + i, &v, 4);
    memcpy(c.mmu.mem2.data() + i, &v, 4);
  }
}

static bool run_case(unsigned regime, uint32_t seed, int* detail) {
  auto A = std::make_unique<CPUContext>(), B = std::make_unique<CPUContext>();
  std::mt19937 qa(seed), qb(seed);
  setup(*A, qa, regime, qa());
  setup(*B, qb, regime, qb());
  g_fifo.clear(); ref_o2::NWII_REF_FUNC(*A); std::vector<uint8_t> fa = g_fifo;
  g_fifo.clear();
#ifdef HAVE_IR
#ifdef NWII_IR_CPP_NAME
  NWII_IR_CPP_NAME(*B);
#elif defined(NWII_IR_C_NAME)
  NWII_IR_C_NAME(*B);
#else
  func_ir_o2(*B);
#endif
#else
  ref_os::NWII_REF_FUNC(*B);
#endif
  std::vector<uint8_t> fb = g_fifo;
  if (memcmp(A.get(), B.get(), kGuestBytes)) { *detail = 1; return false; }
  if (A->inst_count != B->inst_count) { *detail = 2; return false; }
  if (A->exception_pc != B->exception_pc) { *detail = 3; return false; }
  if (memcmp(A->mmu.mem1.data(), B->mmu.mem1.data(), kMemWindow)) { *detail = 4; return false; }
  if (memcmp(A->mmu.mem2.data(), B->mmu.mem2.data(), kMemWindow)) { *detail = 5; return false; }
  if (fa != fb) { *detail = 6; return false; }
  return true;
}

static uint64_t time_min(CPUContext& c, void (*fn)(CPUContext&), int iters, int reps, uint32_t r3) {
  uint64_t best = ~0ull;
  for (int r = 0; r < reps; ++r) {
    _mm_lfence();
    uint64_t t0 = __rdtsc();
    _mm_lfence();
    for (int i = 0; i < iters; ++i) {
      c.gpr[3] = r3;
      c.pc = kEntry;
      c.inst_count = 1;
      c.gpr[13] = 0x80100000u + 0x1000u + 30488u;
      fn(c);
    }
    _mm_lfence();
    uint64_t t1 = __rdtsc();
    _mm_lfence();
    best = std::min(best, t1 - t0);
  }
  return best / iters;
}

static void noop(CPUContext&) {}

int main(int argc, char** argv) {
  int trials = argc > 1 ? atoi(argv[1]) : 4000;
  bool timeonly = argc > 2 && strcmp(argv[2], "timeonly") == 0;
  bool notime = argc > 2 && strcmp(argv[2], "notime") == 0;
  if (!timeonly) {
#ifdef HAVE_IR
    int bad = 0;
    for (unsigned regime = 0; regime < 8; ++regime)
      for (int t = 0; t < trials; ++t) {
        int detail = 0;
        if (!run_case(regime, 0x1234567u + t * 2654435761u + regime * 7919u, &detail)) {
          if (bad < 8) printf("MISMATCH regime=%u seed=%d detail=%d\n", regime, t, detail);
          ++bad;
        }
      }
    printf("EQUIV trials_per_regime=%d regimes=8 total=%d mismatches=%d\n", trials, trials * 8, bad);
    if (bad) return 1;
#else
    (void)trials;
#endif
  }

  if (notime) return 0;

  auto C = std::make_unique<CPUContext>();
  std::mt19937 q(999);
  setup(*C, q, 0, q());
  volatile uint32_t sink = 0;
  struct V { const char* name; void (*fn)(CPUContext&); };
  V vs[] = {
    {"looponly", noop},
    {"cpp_o2", ref_o2::NWII_REF_FUNC},
    {"cpp_os", ref_os::NWII_REF_FUNC},
#ifdef HAVE_IR
#ifdef NWII_IR_CPP_NAME
    {"ir_cpp", NWII_IR_CPP_NAME},
#elif defined(NWII_IR_C_NAME)
    {"ir_c  ", NWII_IR_C_NAME},
#else
    {"ir_o2 ", func_ir_o2},
    {"ir_os ", func_ir_os},
#endif
#endif
  };
  const int nv = sizeof(vs) / sizeof(vs[0]);
  const int iters = 2000000, reps = 30;
  uint64_t best[8][2];
  for (int i = 0; i < nv; ++i) best[i][0] = best[i][1] = ~0ull;
  // warmup
  for (auto& v : vs) for (int i = 0; i < 200000; ++i) { C->gpr[3] = 5; C->pc = kEntry; C->inst_count = 1; C->gpr[13] = 0x80100000u + 0x1000u + 30488u; v.fn(*C); }
  // interleaved rounds so frequency drift cancels
  for (int rep = 0; rep < reps; ++rep)
    for (int i = 0; i < nv; ++i)
      for (int k = 0; k < 2; ++k) {
        uint32_t r3 = k ? 0u : 5u;
        uint64_t cyc = time_min(*C, vs[i].fn, iters, 1, r3);
        if (cyc < best[i][k]) best[i][k] = cyc;
      }
  for (int i = 0; i < nv; ++i) {
    sink = C->gpr[3];
    printf("TIME %s r3=5 cycles_per_call=%llu\n", vs[i].name, (unsigned long long)best[i][0]);
    printf("TIME %s r3=0 cycles_per_call=%llu\n", vs[i].name, (unsigned long long)best[i][1]);
  }
  printf("sink=%u\n", sink);
  return 0;
}
