// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the source-backed native lowerings of
// runtime-overlays/native_lower_math.h.
//
// The translated guest function comes from the generated chunk; the driver
// (tools/check_native_lowering.py) extracts it, routes its memory accesses
// to the harness recorder and writes guest_lowering_extracted.h.  When that
// header is not available (a stand-alone build) the same extracted bodies
// are used from the fallback below.
//
// For every adopted candidate the test runs the translated function and the
// native lowering on identical input states and compares
//
//   * the ordered guest memory effect log,
//   * the ordered GX hardware port log,
//   * the guest RAM in the ranges the two paths touched,
//   * the final guest register file, condition register, LR/CTR/XER/PC/MSR/
//     FPSCR and the retired guest instruction count.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"

#if __has_include("guest_lowering_extracted.h")
#include "guest_lowering_extracted.h"
#else
namespace nl = nwii::runtime::native_lower_test;

// ---- extracted from output_15.cpp ----
NWII_GUEST_ABI void func_800D5370(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x800D5370, ctx);
    if (ctx.pc != 0x800D5370) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_800D5370: ;
    ctx.inst_count += 1;
    // 0x800D5370: 2C04FFFF
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < -1);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > -1);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == -1);
loc_800D5374: ;
    // 0x800D5374: 4082000C
    if (!ctx.cr[0].eq) goto loc_800D5380;
    ++ctx.inst_count;
loc_800D5378: ;
    ctx.inst_count += 1;
    // 0x800D5378: 3860FFFF
    ctx.gpr[3] = -1; // li r3, -1
loc_800D537C: ;
    // 0x800D537C: 4E800020
    ctx.pc = ctx.lr; return;
    ++ctx.inst_count;
loc_800D5380: ;
    ctx.inst_count += 3;
    // 0x800D5380: 5480103A
    ctx.gpr[0] = std::rotl(ctx.gpr[4], 2) & 0xFFFFFFFC; // rlwinm r0, r4, 2, 0, 29
loc_800D5384: ;
    // 0x800D5384: 7C630214
    ctx.gpr[3] = ctx.gpr[3] + ctx.gpr[0];
loc_800D5388: ;
    // 0x800D5388: 806343E0
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 17376); // lwz r3, 17376(r3)
loc_800D538C: ;
    // 0x800D5388: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from output_42.cpp ----
NWII_GUEST_ABI void func_80231DFC(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x80231DFC, ctx);
    if (ctx.pc != 0x80231DFC) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80231DFC: ;
    ctx.inst_count += 3;
    // 0x80231DFC: 80030000
    ctx.gpr[0] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 0); // lwz r0, 0(r3)
loc_80231E00: ;
    // 0x80231E00: 5400843E
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 16) & 0xFFFF; // rlwinm r0, r0, 16, 16, 31
loc_80231E04: ;
    // 0x80231E04: 2800FFFF
    ctx.cr[0].lt = (ctx.gpr[0] < 65535);
    ctx.cr[0].gt = (ctx.gpr[0] > 65535);
    ctx.cr[0].eq = (ctx.gpr[0] == 65535);
loc_80231E08: ;
    // 0x80231E08: 4082000C
    if (!ctx.cr[0].eq) goto loc_80231E14;
    ++ctx.inst_count;
loc_80231E0C: ;
    ctx.inst_count += 1;
    // 0x80231E0C: 38600000
    ctx.gpr[3] = 0; // li r3, 0
loc_80231E10: ;
    // 0x80231E10: 4E800020
    ctx.pc = ctx.lr; return;
    ++ctx.inst_count;
loc_80231E14: ;
    ctx.inst_count += 4;
    // 0x80231E14: 806DCF00
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[13] + -12544); // lwz r3, -12544(r13)
loc_80231E18: ;
    // 0x80231E18: 54001838
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 3) & 0xFFFFFFF8; // rlwinm r0, r0, 3, 0, 28
loc_80231E1C: ;
    // 0x80231E1C: 80630020
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 32); // lwz r3, 32(r3)
loc_80231E20: ;
    // 0x80231E20: 7C630214
    ctx.gpr[3] = ctx.gpr[3] + ctx.gpr[0];
loc_80231E24: ;
    // 0x80231E24: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from output_50.cpp ----
NWII_GUEST_ABI void func_80292364(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x80292364, ctx);
    if (ctx.pc != 0x80292364) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80292364: ;
    ctx.inst_count += 8;
    // 0x80292364: 8103004C
    ctx.gpr[8] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 76); // lwz r8, 76(r3)
loc_80292368: ;
    // 0x80292368: 98880018
    nl::rec_write8(ctx.mmu,ctx.gpr[8] + 24, (uint8_t)ctx.gpr[4]); // stb r4, 24(r8)
loc_8029236C: ;
    // 0x8029236C: 8083004C
    ctx.gpr[4] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 76); // lwz r4, 76(r3)
loc_80292370: ;
    // 0x80292370: 98A40019
    nl::rec_write8(ctx.mmu,ctx.gpr[4] + 25, (uint8_t)ctx.gpr[5]); // stb r5, 25(r4)
loc_80292374: ;
    // 0x80292374: 8083004C
    ctx.gpr[4] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 76); // lwz r4, 76(r3)
loc_80292378: ;
    // 0x80292378: 98C4001A
    nl::rec_write8(ctx.mmu,ctx.gpr[4] + 26, (uint8_t)ctx.gpr[6]); // stb r6, 26(r4)
loc_8029237C: ;
    // 0x8029237C: 8063004C
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 76); // lwz r3, 76(r3)
loc_80292380: ;
    // 0x80292380: 98E3001B
    nl::rec_write8(ctx.mmu,ctx.gpr[3] + 27, (uint8_t)ctx.gpr[7]); // stb r7, 27(r3)
loc_80292384: ;
    // 0x80292384: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from output_53.cpp ----
NWII_GUEST_ABI void func_802B6CF4(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x802B6CF4, ctx);
    if (ctx.pc != 0x802B6CF4) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_802B6CF4: ;
    ctx.inst_count += 3;
    // 0x802B6CF4: 80A30004
    ctx.gpr[5] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 4); // lwz r5, 4(r3)
loc_802B6CF8: ;
    // 0x802B6CF8: 80040004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu,ctx.gpr[4] + 4); // lwz r0, 4(r4)
loc_802B6CFC: ;
    // 0x802B6CFC: 7C050040
    ctx.cr[0].lt = (ctx.gpr[5] < ctx.gpr[0]);
    ctx.cr[0].gt = (ctx.gpr[5] > ctx.gpr[0]);
    ctx.cr[0].eq = (ctx.gpr[5] == ctx.gpr[0]);
    ctx.cr[0].so = (ctx.xer >> 31) & 1;
loc_802B6D00: ;
    // 0x802B6D00: 4182000C
    if (ctx.cr[0].eq) goto loc_802B6D0C;
    ++ctx.inst_count;
loc_802B6D04: ;
    ctx.inst_count += 1;
    // 0x802B6D04: 38600000
    ctx.gpr[3] = 0; // li r3, 0
loc_802B6D08: ;
    // 0x802B6D08: 4E800020
    ctx.pc = ctx.lr; return;
    ++ctx.inst_count;
loc_802B6D0C: ;
    ctx.inst_count += 5;
    // 0x802B6D0C: 80630000
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 0); // lwz r3, 0(r3)
loc_802B6D10: ;
    // 0x802B6D10: 80040000
    ctx.gpr[0] = nl::rec_read32(ctx.mmu,ctx.gpr[4] + 0); // lwz r0, 0(r4)
loc_802B6D14: ;
    // 0x802B6D14: 7C030214
    ctx.gpr[0] = ctx.gpr[0] - ctx.gpr[3]; // subf
loc_802B6D18: ;
    // 0x802B6D18: 7C000034
    ctx.gpr[0] = ctx.gpr[0] == 0 ? 32 : std::countl_zero(ctx.gpr[0]); // cntlzw
loc_802B6D1C: ;
    // 0x802B6D1C: 5403D97E
    ctx.gpr[3] = std::rotl(ctx.gpr[0], 27) & 0x7FFFFFF; // rlwinm r3, r0, 27, 5, 31
loc_802B6D20: ;
    // 0x802B6D20: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from output_62.cpp ----
NWII_GUEST_ABI void func_80321E38(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x80321E38, ctx);
    if (ctx.pc != 0x80321E38) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80321E38: ;
    ctx.inst_count += 8;
    // 0x80321E38: 80830098
    ctx.gpr[4] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 152); // lwz r4, 152(r3)
loc_80321E3C: ;
    // 0x80321E3C: 80030040
    ctx.gpr[0] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 64); // lwz r0, 64(r3)
loc_80321E40: ;
    // 0x80321E40: 80640014
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[4] + 20); // lwz r3, 20(r4)
loc_80321E44: ;
    // 0x80321E44: 5400103A
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 2) & 0xFFFFFFFC; // rlwinm r0, r0, 2, 0, 29
loc_80321E48: ;
    // 0x80321E48: 7C63002E
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + ctx.gpr[0]); // lwzx
loc_80321E4C: ;
    // 0x80321E4C: 80630010
    ctx.gpr[3] = nl::rec_read32(ctx.mmu,ctx.gpr[3] + 16); // lwz r3, 16(r3)
loc_80321E50: ;
    // 0x80321E50: 8803000A
    ctx.gpr[0] = nl::rec_read8(ctx.mmu,ctx.gpr[3] + 10); // lbz r0, 10(r3)
loc_80321E54: ;
    // 0x80321E54: 5403DFFE
    ctx.gpr[3] = std::rotl(ctx.gpr[0], 27) & 0x1; // rlwinm r3, r0, 27, 31, 31
loc_80321E58: ;
    // 0x80321E58: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from output_89.cpp ----
NWII_GUEST_ABI void func_8046616C(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8046616C, ctx);
    if (ctx.pc != 0x8046616C) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8046616C: ;
    ctx.inst_count += 9;
    // 0x8046616C: 3CA08059
    ctx.gpr[5] = -2141650944; // lis
loc_80466170: ;
    // 0x8046616C: 3C600019
    ctx.gpr[3] = 1638400; // lis
loc_80466174: ;
    // 0x8046616C: 8085F590
    ctx.gpr[4] = nl::rec_read32(ctx.mmu,ctx.gpr[5] + -2672); // lwz r4, -2672(r5)
loc_80466178: ;
    // 0x8046616C: 3803660D
    ctx.gpr[0] = ctx.gpr[3] + 26125; // addi r0, r3, 26125
loc_8046617C: ;
    // 0x8046617C: 7C6401D6
    ctx.gpr[3] = ctx.gpr[4] * ctx.gpr[0]; // mullw
loc_80466180: ;
    // 0x8046616C: 3C633C6F
    ctx.gpr[3] = ctx.gpr[3] + 1013907456; // addis
loc_80466184: ;
    // 0x8046616C: 3803F35F
    ctx.gpr[0] = ctx.gpr[3] + -3233; // addi r0, r3, -3233
loc_80466188: ;
    // 0x8046616C: 9005F590
    nl::rec_write32(ctx.mmu,ctx.gpr[5] + -2672, ctx.gpr[0]); // stw r0, -2672(r5)
loc_8046618C: ;
    // 0x8046616C: 5403843E
    ctx.gpr[3] = std::rotl(ctx.gpr[0], 16) & 0xFFFF; // rlwinm r3, r0, 16, 16, 31
loc_80466190: ;
    // 0x8046616C: 4E800020
    ctx.pc = ctx.lr; return;
}
#endif  // __has_include("guest_lowering_extracted.h")

#if __has_include("native_lower_math.h")
#include "native_lower_math.h"
#else
#include "../runtime-overlays/native_lower_math.h"
#endif

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace nwii::runtime {
void GX_WGPIPE_Write8(uint8_t v) { native_lower_test::record_port(0xCC008000, 1, v); }
void GX_WGPIPE_Write16(uint16_t v) { native_lower_test::record_port(0xCC008000, 2, v); }
void GX_WGPIPE_Write32(uint32_t v) { native_lower_test::record_port(0xCC008000, 4, v); }
void GX_WGPIPE_WriteF32(float v) { uint32_t b; std::memcpy(&b, &v, 4); native_lower_test::record_port(0xCC008000, 4, b); }
void GX_WGPIPE_WriteF64(double v) { uint64_t b; std::memcpy(&b, &v, 8); native_lower_test::record_port(0xCC008000, 8, b); }
void GX_DL_Snapshot(uint32_t, uint32_t) {}
bool GX_DL_Fetch(uint32_t, uint32_t, std::vector<uint8_t>&) { return false; }
bool native_gx_emitter_ready() { return false; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
void HW_Reg_Write16(uint32_t a, uint16_t v) { native_lower_test::record_port(a, 2, v); }
void HW_Reg_Write32(uint32_t a, uint32_t v) { native_lower_test::record_port(a, 4, v); }
bool g_native_fifo_ok = false;
bool process_pending_callbacks(CPUContext&) { return false; }
int32_t g_native_poll_credit = 0;
void watch_hit(uint32_t, uint32_t, int) {}
uint32_t g_watch_addr = 0;
bool g_trace_calls = false;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) {}
}  // namespace nwii::runtime

namespace nl = nwii::runtime::native_lower_test;
namespace nm = nwii::runtime::native_lower;
using nwii::runtime::CPUContext;

// ---------------------------------------------------------------------------
// Test driver
// ---------------------------------------------------------------------------

namespace {

// The two guest states the two paths run on.  They are reused across the
// cases: every case writes the same setup into both, so the two states stay
// identical.
CPUContext g_a;
CPUContext g_b;

volatile uint64_t g_sink = 0;

void reset_state(CPUContext& ctx) {
  for (int i = 0; i < 32; ++i) {
    ctx.gpr[i] = 0;
    ctx.fpr[i] = 0.0;
    ctx.ps1[i] = 0.0;
  }
  for (int i = 0; i < 8; ++i) ctx.cr[i] = nwii::runtime::ConditionField();
  ctx.lr = 0;
  ctx.ctr = 0;
  ctx.xer = 0;
  ctx.pc = 0;
  ctx.msr = 0;
  ctx.fpscr = 0;
  ctx.inst_count = 0;
}

void put32(CPUContext& ctx, uint32_t address, uint32_t value) {
  ctx.mmu.write32(address, value);
}
void put8(CPUContext& ctx, uint32_t address, uint8_t value) {
  ctx.mmu.write8(address, value);
}

struct Group {
  const char* name;
  int cases = 0;
  int failures = 0;
};

Group g_groups[8];
int g_group_count = 0;

Group& group(const char* name) {
  for (int i = 0; i < g_group_count; ++i)
    if (std::strcmp(g_groups[i].name, name) == 0) return g_groups[i];
  g_groups[g_group_count].name = name;
  return g_groups[g_group_count++];
}

// Runs the translated function and the native lowering on identical states
// and compares everything the guest can observe.
template <typename Setup, typename Translated, typename Native>
void run_pair(Group& grp, const std::string& label, const nl::VolatileSet& vol,
              Setup setup, Translated translated, Native native) {
  reset_state(g_a);
  reset_state(g_b);
  setup(g_a);
  setup(g_b);

  nl::g_effects.clear();
  nl::g_ports.clear();
  nl::g_recording = true;
  translated(g_a);
  const std::vector<nl::Effect> original = nl::g_effects.entries;
  const std::vector<nl::Effect> original_ports = nl::g_ports.entries;

  nl::g_effects.clear();
  nl::g_ports.clear();
  native(g_b);
  const std::vector<nl::Effect> lowered = nl::g_effects.entries;
  const std::vector<nl::Effect> lowered_ports = nl::g_ports.entries;
  nl::g_recording = false;

  nl::Difference d = nl::compare(nl::capture(g_a), nl::capture(g_b), vol);
  nl::Difference e = nl::compare_effects(original, lowered, "effect");
  if (e.any) d.note(e.text);
  e = nl::compare_effects(original_ports, lowered_ports, "port");
  if (e.any) d.note(e.text);
  std::vector<nl::Region> regions = nl::touched_regions(original, lowered);
  e = nl::compare_ram(g_a.mmu, g_b.mmu, regions);
  if (e.any) d.note(e.text);

  ++grp.cases;
  if (d.any) {
    ++grp.failures;
    if (grp.failures <= 4)
      std::printf("FAIL %s [%s]\n%s", grp.name, label.c_str(), d.text.c_str());
  }
}

// ===========================================================================
// 0x802B6CF4 - range_equal_probe
// ===========================================================================
void test_range_equal_probe() {
  Group& grp = group("math.range_equal_probe");
  nl::VolatileSet vol;
  vol.allow_gpr(0);   // r0, scratch
  vol.allow_gpr(5);   // r5, scratch
  vol.allow_cr(0);    // CR0, volatile

  const uint32_t kA = 0x80410000u;
  const uint32_t kB = 0x80410040u;

  struct Case { uint32_t a_lo, a_hi, b_lo, b_hi; const char* label; };
  const Case cases[] = {
      {0u, 0u, 0u, 0u, "both zero"},
      {1u, 0u, 1u, 0u, "both one"},
      {0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu, "all ones"},
      {0u, 0u, 1u, 0u, "low words differ by one"},
      {0u, 0u, 2u, 0u, "low words differ by two"},
      {0u, 0u, 4u, 0u, "low words differ by four"},
      {0u, 0u, 0x80000000u, 0u, "low words differ by 0x80000000"},
      {0u, 0u, 0xFFFFFFFFu, 0u, "low words differ by all ones"},
      {0u, 1u, 0u, 0u, "high words differ"},
      {0u, 0xFFFFFFFFu, 0u, 0x7FFFFFFFu, "high words differ (max)"},
      {0x80000000u, 0u, 0x80000000u, 0u, "equal negative low word"},
      {0x7FFFFFFFu, 0x12345678u, 0x7FFFFFFFu, 0x12345678u, "equal random"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kA + 0, c.a_lo);
               put32(ctx, kA + 4, c.a_hi);
               put32(ctx, kB + 0, c.b_lo);
               put32(ctx, kB + 4, c.b_hi);
               ctx.gpr[3] = kA;
               ctx.gpr[4] = kB;
               ctx.pc = 0x802B6CF4u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_802B6CF4(ctx); },
             [](CPUContext& ctx) { nm::apply_range_equal_probe(ctx); });
  }

  // The two pointers are the same object.
  run_pair(grp, "same pointer", vol,
           [&](CPUContext& ctx) {
             put32(ctx, kA + 0, 0x12345678u);
             put32(ctx, kA + 4, 0x9ABCDEF0u);
             ctx.gpr[3] = kA;
             ctx.gpr[4] = kA;
             ctx.pc = 0x802B6CF4u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_802B6CF4(ctx); },
           [](CPUContext& ctx) { nm::apply_range_equal_probe(ctx); });

  // The second pointer is below the first one.
  run_pair(grp, "second pointer below the first", vol,
           [&](CPUContext& ctx) {
             put32(ctx, kA + 0, 7u);
             put32(ctx, kA + 4, 7u);
             put32(ctx, kB + 0, 7u);
             put32(ctx, kB + 4, 8u);
             ctx.gpr[3] = kA;
             ctx.gpr[4] = kB;
             ctx.pc = 0x802B6CF4u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_802B6CF4(ctx); },
           [](CPUContext& ctx) { nm::apply_range_equal_probe(ctx); });

  // Randomised ranges.
  std::mt19937 rng(0x5EED1234u);
  for (int i = 0; i < 32; ++i) {
    const uint32_t a_lo = rng();
    const uint32_t a_hi = rng();
    uint32_t b_lo = rng();
    uint32_t b_hi = rng();
    if (i % 3 == 0) b_hi = a_hi;    // equal high words
    if (i % 5 == 0) b_lo = a_lo;     // fully equal
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kA + 0, a_lo);
               put32(ctx, kA + 4, a_hi);
               put32(ctx, kB + 0, b_lo);
               put32(ctx, kB + 4, b_hi);
               ctx.gpr[3] = kA;
               ctx.gpr[4] = kB;
               ctx.pc = 0x802B6CF4u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_802B6CF4(ctx); },
             [](CPUContext& ctx) { nm::apply_range_equal_probe(ctx); });
  }
}

// ===========================================================================
// 0x80231DFC - sda2_table_lookup
// ===========================================================================
void test_sda2_table_lookup() {
  Group& grp = group("math.sda2_table_lookup");
  nl::VolatileSet vol;
  vol.allow_gpr(0);
  vol.allow_cr(0);

  const uint32_t kSda2 = 0x8059A0D0u;      // the game's small data area 2 base
  const uint32_t kGlobal = kSda2 - 12544u; // 0x80596FE0
  const uint32_t kHolder = 0x80420000u;
  const uint32_t kTable = 0x80430000u;
  const uint32_t kKey = 0x80440000u;

  struct Case { uint32_t key; const char* label; };
  const Case cases[] = {
      {0x00000000u, "index 0"},
      {0x00000001u, "index 0 (low word only)"},
      {0x00010000u, "index 1"},
      {0x00020000u, "index 2"},
      {0xFFFE0000u, "index 0xFFFE"},
      {0xFFFF0000u, "index 0xFFFF (early return)"},
      {0xFFFFFFFFu, "index 0xFFFF (all ones)"},
      {0x0000FFFFu, "index 0, low word 0xFFFF"},
      {0x1234ABCDu, "random key"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kGlobal, kHolder);
               put32(ctx, kHolder + 32, kTable);
               put32(ctx, kKey, c.key);
               ctx.gpr[13] = kSda2;
               ctx.gpr[3] = kKey;
               ctx.pc = 0x80231DFCu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_80231DFC(ctx); },
             [](CPUContext& ctx) { nm::apply_sda2_table_lookup(ctx); });
  }

  // The table holder is null: the guest reads address 0x20.
  run_pair(grp, "null table holder", vol,
           [&](CPUContext& ctx) {
             put32(ctx, kGlobal, 0u);
             put32(ctx, 0x20u, 0x00C0FFEEu);
             put32(ctx, kKey, 0x00030000u);
             ctx.gpr[13] = kSda2;
             ctx.gpr[3] = kKey;
             ctx.pc = 0x80231DFCu;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_80231DFC(ctx); },
           [](CPUContext& ctx) { nm::apply_sda2_table_lookup(ctx); });

  std::mt19937 rng(0x5EED5678u);
  for (int i = 0; i < 24; ++i) {
    const uint32_t key = rng();
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kGlobal, kHolder);
               put32(ctx, kHolder + 32, kTable);
               put32(ctx, kKey, key);
               ctx.gpr[13] = kSda2;
               ctx.gpr[3] = kKey;
               ctx.pc = 0x80231DFCu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_80231DFC(ctx); },
             [](CPUContext& ctx) { nm::apply_sda2_table_lookup(ctx); });
  }
}

// ===========================================================================
// 0x800D5370 - indexed_slot_load
// ===========================================================================
void test_indexed_slot_load() {
  Group& grp = group("math.indexed_slot_load");
  nl::VolatileSet vol;
  vol.allow_gpr(0);
  vol.allow_cr(0);

  const uint32_t kTable = 0x80450000u;
  const uint32_t kSlots = kTable + 17376u;

  struct Case { uint32_t index; const char* label; };
  const Case cases[] = {
      {0xFFFFFFFFu, "index -1"},
      {0x00000000u, "index 0"},
      {0x00000001u, "index 1"},
      {0x00000007u, "index 7"},
      {0x00000010u, "index 16"},
      {0x40000000u, "index 0x40000000 (scaled to zero)"},
      {0x3FFFFFFFu, "index 0x3FFFFFFF (address wraps)"},
      {0x80000000u, "index 0x80000000 (address wraps)"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               for (uint32_t i = 0; i < 32; ++i) put32(ctx, kSlots + i * 4u, 0x1000u + i);
               ctx.gpr[3] = kTable;
               ctx.gpr[4] = c.index;
               ctx.pc = 0x800D5370u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_800D5370(ctx); },
             [](CPUContext& ctx) { nm::apply_indexed_slot_load(ctx); });
  }

  std::mt19937 rng(0x5EED9012u);
  for (int i = 0; i < 24; ++i) {
    const uint32_t index = (i % 4 == 0) ? rng() : (rng() % 64u);
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               for (uint32_t k = 0; k < 64; ++k) put32(ctx, kSlots + k * 4u, 0xA0000000u + k);
               ctx.gpr[3] = kTable;
               ctx.gpr[4] = index;
               ctx.pc = 0x800D5370u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_800D5370(ctx); },
             [](CPUContext& ctx) { nm::apply_indexed_slot_load(ctx); });
  }
}

// ===========================================================================
// 0x80321E38 - object_flag_bit
// ===========================================================================
void test_object_flag_bit() {
  Group& grp = group("math.object_flag_bit");
  nl::VolatileSet vol;
  vol.allow_gpr(0);
  vol.allow_gpr(4);

  const uint32_t kObject = 0x80460000u;
  const uint32_t kNode = 0x80461000u;
  const uint32_t kSlot = 0x80462000u;
  const uint32_t kEntry = 0x80463000u;
  const uint32_t kLeaf = 0x80464000u;

  auto setup_chain = [&](CPUContext& ctx, uint32_t index, uint8_t flags) {
    put32(ctx, kObject + 0x98u, kNode);
    put32(ctx, kObject + 0x40u, index);
    put32(ctx, kNode + 0x14u, kSlot);
    put32(ctx, kSlot + index * 4u, kEntry);
    put32(ctx, kEntry + 0x10u, kLeaf);
    put8(ctx, kLeaf + 10u, flags);
    ctx.gpr[3] = kObject;
    ctx.pc = 0x80321E38u;
    ctx.lr = 0x80010000u;
  };

  const uint8_t flag_values[] = {0x00u, 0x1Fu, 0x20u, 0x21u, 0x3Fu, 0x40u, 0xFFu};
  for (uint8_t flags : flag_values) {
    char label[64];
    std::snprintf(label, sizeof(label), "flags 0x%02X", flags);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) { setup_chain(ctx, 0u, flags); },
             [](CPUContext& ctx) { func_80321E38(ctx); },
             [](CPUContext& ctx) { nm::apply_object_flag_bit(ctx); });
  }

  for (uint32_t index : {1u, 2u, 3u, 7u}) {
    char label[64];
    std::snprintf(label, sizeof(label), "index %u", index);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) { setup_chain(ctx, index, 0x20u); },
             [](CPUContext& ctx) { func_80321E38(ctx); },
             [](CPUContext& ctx) { nm::apply_object_flag_bit(ctx); });
  }

  std::mt19937 rng(0x5EEDA11Au);
  for (int i = 0; i < 24; ++i) {
    const uint32_t index = rng() % 8u;
    const uint8_t flags = static_cast<uint8_t>(rng());
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) { setup_chain(ctx, index, flags); },
             [](CPUContext& ctx) { func_80321E38(ctx); },
             [](CPUContext& ctx) { nm::apply_object_flag_bit(ctx); });
  }
}

// ===========================================================================
// 0x80292364 - store_byte_quad
// ===========================================================================
void test_store_byte_quad() {
  Group& grp = group("math.store_byte_quad");
  nl::VolatileSet vol;
  vol.allow_gpr(4);
  vol.allow_gpr(8);

  const uint32_t kObject = 0x80470000u;
  const uint32_t kTarget = 0x80471000u;

  struct Case { uint32_t b0, b1, b2, b3; const char* label; };
  const Case cases[] = {
      {0u, 0u, 0u, 0u, "all zero"},
      {0xFFu, 0xFFu, 0xFFu, 0xFFu, "all ones"},
      {0x78u, 0x56u, 0x34u, 0x12u, "distinct bytes"},
      {0x12345678u, 0x9ABCDEF0u, 0x11223344u, 0x55667788u, "full words (low byte only)"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kObject + 0x4Cu, kTarget);
               for (uint32_t i = 0; i < 8; ++i) put8(ctx, kTarget + 0x18u + i, 0xEEu);
               ctx.gpr[3] = kObject;
               ctx.gpr[4] = c.b0;
               ctx.gpr[5] = c.b1;
               ctx.gpr[6] = c.b2;
               ctx.gpr[7] = c.b3;
               ctx.pc = 0x80292364u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_80292364(ctx); },
             [](CPUContext& ctx) { nm::apply_store_byte_quad(ctx); });
  }

  // Aliasing: the pointer slot itself is one of the written bytes, so the
  // second load of the pointer observes the first store.
  run_pair(grp, "pointer slot aliases the first store", vol,
           [&](CPUContext& ctx) {
             const uint32_t aliased = kObject + 0x4Cu - 0x18u;
             put32(ctx, kObject + 0x4Cu, aliased);
             ctx.gpr[3] = kObject;
             ctx.gpr[4] = 0x41u;
             ctx.gpr[5] = 0x42u;
             ctx.gpr[6] = 0x43u;
             ctx.gpr[7] = 0x44u;
             ctx.pc = 0x80292364u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_80292364(ctx); },
           [](CPUContext& ctx) { nm::apply_store_byte_quad(ctx); });

  std::mt19937 rng(0x5EEDBEEFu);
  for (int i = 0; i < 24; ++i) {
    const uint32_t b0 = rng(), b1 = rng(), b2 = rng(), b3 = rng();
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kObject + 0x4Cu, kTarget);
               ctx.gpr[3] = kObject;
               ctx.gpr[4] = b0;
               ctx.gpr[5] = b1;
               ctx.gpr[6] = b2;
               ctx.gpr[7] = b3;
               ctx.pc = 0x80292364u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_80292364(ctx); },
             [](CPUContext& ctx) { nm::apply_store_byte_quad(ctx); });
  }
}

// ===========================================================================
// 0x8046616C - lcg_step
// ===========================================================================
void test_lcg_step() {
  Group& grp = group("math.lcg_step");
  nl::VolatileSet vol;
  vol.allow_gpr(0);
  vol.allow_gpr(4);
  vol.allow_gpr(5);

  const uint32_t kState = nm::kLcgStateAddress;

  struct Case { uint32_t state; const char* label; };
  const Case cases[] = {
      {0u, "state zero"},
      {1u, "state one"},
      {0xFFFFFFFFu, "state all ones"},
      {0x3C6EF35Fu, "state equals the increment"},
      {0x12345678u, "state random"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kState, c.state);
               ctx.pc = 0x8046616Cu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_8046616C(ctx); },
             [](CPUContext& ctx) { nm::apply_lcg_step(ctx); });
  }

  std::mt19937 rng(0x5EEDCAFEu);
  for (int i = 0; i < 24; ++i) {
    const uint32_t state = rng();
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kState, state);
               ctx.pc = 0x8046616Cu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_8046616C(ctx); },
             [](CPUContext& ctx) { nm::apply_lcg_step(ctx); });
  }
}

// ===========================================================================
// Paired micro-benchmark
//
// The translated function and the native lowering run alternately on the same
// input state; the results are consumed so the compiler cannot delete the
// work.  The reported numbers are a local component measurement (host
// nanoseconds per call), not a whole-game frame rate.
// ===========================================================================
template <typename F>
double sample_ns(F&& body, int iterations) {
  const auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < iterations; ++i) {
    // A compiler barrier keeps the guest loads inside the loop: without it
    // the compiler hoists the (loop invariant) reads of the translated
    // function out of the benchmark and measures nothing.
    asm volatile("" ::: "memory");
    body();
  }
  const auto t1 = std::chrono::steady_clock::now();
  return std::chrono::duration<double, std::nano>(t1 - t0).count() / iterations;
}

double median_of(std::vector<double> samples) {
  std::sort(samples.begin(), samples.end());
  return samples[samples.size() / 2];
}

template <typename Translated, typename Native>
void benchmark(const char* name, uint32_t address, Translated translated, Native native, const char* note) {
  reset_state(g_a);
  g_a.lr = 0x80010000u;
  nl::g_recording = false;

  const int kIterations = 20000;
  const int kRepeats = 9;

  // The guest instructions both paths retire for one call.
  g_a.pc = address;
  const uint64_t before = g_a.inst_count;
  translated(g_a);
  const uint64_t translated_insts = g_a.inst_count - before;
  g_a.pc = address;
  const uint64_t before2 = g_a.inst_count;
  native(g_a);
  const uint64_t native_insts = g_a.inst_count - before2;

  for (int i = 0; i < 2000; ++i) {  // warm up
    g_a.pc = address;
    translated(g_a);
    g_a.pc = address;
    native(g_a);
  }

  std::vector<double> translated_samples;
  std::vector<double> native_samples;
  for (int r = 0; r < kRepeats; ++r) {  // alternate the two paths
    // The staged entry is only taken at the function's own address, so the
    // benchmark re-enters it the way the generated dispatcher does.
    translated_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      translated(g_a);
      g_sink += g_a.gpr[3];
    }, kIterations));
    native_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      native(g_a);
      g_sink += g_a.gpr[3];
    }, kIterations));
  }
  const double translated_ns = median_of(translated_samples);
  const double native_ns = median_of(native_samples);

  std::printf("BENCH %-22s translated %8.1f ns/call   native %8.1f ns/call   speedup %5.2fx   "
              "guest insts %llu/%llu   %s\n",
              name, translated_ns, native_ns,
              native_ns > 0.0 ? translated_ns / native_ns : 0.0,
              (unsigned long long)translated_insts, (unsigned long long)native_insts, note);
}

}  // namespace

int main() {
#ifndef NATIVE_LOWERING_TRACE_MEMORY
  std::printf("NATIVE_LOWERING_TESTS note: built without NATIVE_LOWERING_TRACE_MEMORY; the native "
              "path records no memory effects, so the effect-log comparison cannot pass.  The "
              "differential test needs -DNATIVE_LOWERING_TRACE_MEMORY.\n");
#endif
  test_range_equal_probe();
  test_sda2_table_lookup();
  test_indexed_slot_load();
  test_object_flag_bit();
  test_store_byte_quad();
  test_lcg_step();

  int failures = 0;
  for (int i = 0; i < g_group_count; ++i) {
    const Group& g = g_groups[i];
    std::printf("NATIVE_LOWERING_TESTS %s %d/%d\n", g.name, g.cases - g.failures, g.cases);
    failures += g.failures;
  }
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");

  // Paired micro-benchmark.  Both paths see the same input state.
  reset_state(g_a);
  g_a.gpr[3] = 0x80410000u;
  g_a.gpr[4] = 0x80410040u;
  g_a.gpr[5] = 0x80410000u;
  g_a.gpr[6] = 0x80410040u;
  g_a.gpr[7] = 0x80410040u;
  g_a.gpr[13] = 0x8059A0D0u;
  put32(g_a, 0x80410000u, 0x11111111u);
  put32(g_a, 0x80410004u, 0x22222222u);
  put32(g_a, 0x80410040u, 0x11111111u);
  put32(g_a, 0x80410044u, 0x22222222u);
  put32(g_a, 0x8058F590u, 0x12345678u);
  put32(g_a, 0x80596FE0u, 0x80420000u);
  put32(g_a, 0x80420020u, 0x80430000u);
  // The object chain of 0x80321E38.
  put32(g_a, 0x80460098u, 0x80461000u);
  put32(g_a, 0x80460040u, 0u);
  put32(g_a, 0x80461014u, 0x80462000u);
  put32(g_a, 0x80462000u, 0x80463000u);
  put32(g_a, 0x80463010u, 0x80464000u);
  put8(g_a, 0x8046400Au, 0x20u);
  // The slot table of 0x800D5370 and the target of 0x80292364.
  for (uint32_t i = 0; i < 32; ++i) put32(g_a, 0x804543E0u + i * 4u, 0x1000u + i);
  put32(g_a, 0x8047004Cu, 0x80471000u);
  benchmark("802B6CF4", 0x802B6CF4u, func_802B6CF4, nm::apply_range_equal_probe,
            "2 reads, cntlzw compare");
  benchmark("80231DFC", 0x80231DFCu, func_80231DFC, nm::apply_sda2_table_lookup,
            "1-3 reads, SDA2 global");
  benchmark("800D5370", 0x800D5370u, func_800D5370, nm::apply_indexed_slot_load,
            "1 read");
  benchmark("80321E38", 0x80321E38u, func_80321E38, nm::apply_object_flag_bit,
            "6 dependent reads");
  benchmark("80292364", 0x80292364u, func_80292364, nm::apply_store_byte_quad,
            "4 reads + 4 byte writes");
  benchmark("8046616C", 0x8046616Cu, func_8046616C, nm::apply_lcg_step,
            "1 read + 1 write");
  std::printf("BENCH sink %llu\n", (unsigned long long)g_sink);
#ifdef NATIVE_LOWERING_TRACE_MEMORY
  std::printf("BENCH note: this build records every guest memory access, so the native path "
              "pays one extra call per access; the production build (without "
              "NATIVE_LOWERING_TRACE_MEMORY) is the relevant configuration.\n");
#endif

  return failures == 0 ? 0 : 1;
}
