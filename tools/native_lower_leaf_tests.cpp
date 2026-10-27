// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the source-backed native lowerings of
// runtime-overlays/native_lower_leaf.h.
//
// The translated guest function comes from the generated chunk; the driver
// (tools/check_native_lowering.py) extracts it, routes its memory accesses
// to the harness recorder and writes guest_lowering_extracted.h.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"
#include "guest_lowering_extracted.h"
#include "native_lower_support.h"
#include "native_lower_leaf.h"

#include <cstdint>
#include <cstdio>
#include <random>

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
using namespace nwii::runtime;

static void put32(CPUContext& ctx, uint32_t address, uint32_t value) {
  uint32_t bits = __builtin_bswap32(value);
  std::memcpy(ctx.mmu.mem1.data() + (address & 0x3fffffffu), &bits, 4);
}
static void put16(CPUContext& ctx, uint32_t address, uint16_t value) {
  uint16_t bits = __builtin_bswap16(value);
  std::memcpy(ctx.mmu.mem1.data() + (address & 0x3fffffffu), &bits, 2);
}

// --- OSClearContext --------------------------------------------------------
static int test_os_clear_context() {
  std::mt19937 rng(0x0C1E4A5u);
  int cases = 0, failures = 0;
  for (int i = 0; i < 400; ++i) {
    CPUContext a, b;
    const uint32_t context = 0x00110000u + (rng() & 0x3F) * 0x100u;
    const bool points_at_context = (rng() & 1) != 0;
    const uint32_t stored = points_at_context ? context : (0x00200000u + (rng() & 0xFF) * 4u);
    for (CPUContext* ctx : {&a, &b}) {
      ctx->pc = 0x803B5988;
      ctx->lr = 0x80002000;
      ctx->gpr[1] = 0x00300000;
      ctx->gpr[3] = context;
      ctx->gpr[4] = 0x11111111u;
      ctx->gpr[5] = 0x22222222u;
      ctx->msr = 0x2000;
      put16(*ctx, context + 0x1A0, 0xBEEF);
      put16(*ctx, context + 0x1A2, 0xCAFE);
      put32(*ctx, native_lower::kOsCurrentFpuContext, stored);
    }
    nl::g_effects.clear();
    nl::g_ports.clear();
    nl::g_recording = true;
    func_803B5988(a);
    std::vector<nl::Effect> original = nl::g_effects.entries;
    std::vector<nl::Effect> original_ports = nl::g_ports.entries;
    nl::g_effects.clear();
    nl::g_ports.clear();
    native_lower::os_clear_context(native_lower::guest_mem(b), b.inst_count, b.gpr[3]);
    b.gpr[4] = 0x80000000u;   // the staged entry republishes r4
    b.gpr[5] = 0u;            // ... and r5
    b.pc = b.lr;
    std::vector<nl::Effect> native = nl::g_effects.entries;
    std::vector<nl::Effect> native_ports = nl::g_ports.entries;
    nl::g_recording = false;

    nl::VolatileSet vol;
    vol.allow_gpr(0);
    vol.allow_cr(0);
    nl::Difference d = nl::compare(nl::capture(a), nl::capture(b), vol);
    nl::Difference e = nl::compare_effects(original, native, "effect");
    if (e.any) d.note(e.text);
    e = nl::compare_effects(original_ports, native_ports, "port");
    if (e.any) d.note(e.text);
    std::vector<nl::Region> regions = nl::touched_regions(original, native);
    regions.push_back(nl::Region{context + 0x190, context + 0x1B0});
    regions.push_back(nl::Region{native_lower::kOsCurrentFpuContext - 8,
                                 native_lower::kOsCurrentFpuContext + 8});
    e = nl::compare_ram(a.mmu, b.mmu, regions);
    if (e.any) d.note(e.text);
    ++cases;
    if (d.any) {
      ++failures;
      if (failures < 4) std::printf("FAIL os_clear_context case %d\n%s", i, d.text.c_str());
    }
  }
  std::printf("NATIVE_LOWERING_TESTS leaf.os_clear_context %d/%d\n", cases - failures, cases);
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");
  return failures;
}

int main() {
  const int failures = test_os_clear_context();
  return failures == 0 ? 0 : 1;
}
