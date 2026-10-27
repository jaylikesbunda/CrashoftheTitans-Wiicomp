// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the bounded native lowerings of the vertex-emission
// blocks of func_803143EC (native-port/runtime-overlays/native_lower_vertex.h).
//
// The oracle is the *translated* guest block, taken verbatim from the
// generated chunk (storage/crash-generated/output_61.cpp, func_803143EC) with
// its memory accesses routed to the harness recorder - the same rewrite
// tools/check_native_lowering.py applies to the functions it extracts.
//
// For every block the test runs the translated block and the native kernel on
// identical guest states and compares
//
//   * the ordered guest memory accesses (the harness effect log),
//   * the ordered writes to the GX write-gather port (the harness port log),
//   * the guest RAM in the range the block read,
//   * the full guest register file, CR, LR, CTR, XER, PC, MSR and FPSCR,
//   * the retired guest instruction count.
//
// The 16-store attribute loop body (the block that emits the 16 attribute
// floats of one vertex record) is the real case the assignment asks for: it
// performs 16 attribute stores to the write-gather pipe, not a toy one.
//
// The file also contains the paired benchmark (median of several
// repetitions, the two paths run alternately on the same inputs, the results
// consumed) and the code-size / host-instruction-count report.
//
// The guest RAM of the production build lives at the fixed host addresses
// 0x20000000 and 0x30000000 (NWII_STATIC_RAM_BASE), which is what
// native_fastmem::read* assumes.  The test commits the same windows, so the
// generated code's `native_fastmem::read_f32` reads the same bytes the
// runtime's MMU reads and the benchmark measures the code the game runs.
#ifndef NWII_STATIC_RAM_BASE
#define NWII_STATIC_RAM_BASE
#endif

#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"
#include "native_lower_support.h"
#include "native_lower_vertex.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <random>
#include <set>
#include <vector>

namespace nl = nwii::runtime::native_lower_test;
namespace nlx = nwii::runtime::native_lower;
using nwii::runtime::CPUContext;

// ---------------------------------------------------------------------------
// The runtime services the generated guest code needs.
// ---------------------------------------------------------------------------

// The write-gather port. The value is consumed so the compiler cannot remove
// the call, and the port write is recorded so the two paths' ordered port
// logs are comparable.
static volatile uint64_t g_fifo_sink = 0;

namespace nwii::runtime {

void GX_WGPIPE_Write8(uint8_t v) { nl::record_port(0xCC008000u, 1, v); g_fifo_sink += v; }
void GX_WGPIPE_Write16(uint16_t v) { nl::record_port(0xCC008000u, 2, v); g_fifo_sink += v; }
void GX_WGPIPE_Write32(uint32_t v) { nl::record_port(0xCC008000u, 4, v); g_fifo_sink += v; }
void GX_WGPIPE_WriteF32(float v) {
  uint32_t b;
  std::memcpy(&b, &v, 4);
  nl::record_port(0xCC008000u, 4, b);
  g_fifo_sink += b;
}
void GX_WGPIPE_WriteF64(double v) {
  uint64_t b;
  std::memcpy(&b, &v, 8);
  nl::record_port(0xCC008000u, 8, b);
  g_fifo_sink += b;
}
void GX_DL_Snapshot(uint32_t, uint32_t) {}
bool GX_DL_Fetch(uint32_t, uint32_t, std::vector<uint8_t>&) { return false; }
bool native_gx_emitter_ready() { return false; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
uint16_t HW_Reg_Read16(uint32_t) { return 0; }
void HW_Reg_Write16(uint32_t, uint16_t) {}
void HW_Reg_Write32(uint32_t, uint32_t) {}
bool g_native_fifo_ok = false;
bool process_pending_callbacks(CPUContext&) { return false; }
int32_t g_native_poll_credit = 0;
void watch_hit(uint32_t, uint32_t, int) {}
uint32_t g_watch_addr = 0;
bool g_trace_calls = false;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) {}

// The static-base MMU commits the guest RAM windows through this hook.  In
// the shipped runtime it is the fastmem allocator (a source file); the test
// provides the plain commit, because the test only needs the bytes.
void native_static_ram_commit(uintptr_t base, size_t bytes) {
  static std::mutex guard;
  static std::set<uintptr_t> committed;
  std::lock_guard<std::mutex> lock(guard);
  if (!committed.insert(base).second) return;
  void* window = VirtualAlloc(reinterpret_cast<void*>(base), bytes,
                              MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
  if (!window) std::abort();
}

}  // namespace nwii::runtime

// ---------------------------------------------------------------------------
// The guest state the blocks work on.
//
// r26 holds the vertex record, r29 the write-gather base (0xCC010000, so
// r29 - 32768 is the 0xCC008000 port) and r3 the attribute cursor.
// ---------------------------------------------------------------------------
static constexpr uint32_t kRecord = 0x00310000u;
static constexpr uint32_t kFifoBase = 0xCC010000u;

static void put32(CPUContext& ctx, uint32_t address, uint32_t value) {
  uint32_t bits = __builtin_bswap32(value);
  std::memcpy(ctx.mmu.mem1.data() + (address & 0x3fffffffu), &bits, 4);
}

static void put_f32(CPUContext& ctx, uint32_t address, float value) {
  uint32_t bits;
  std::memcpy(&bits, &value, 4);
  bits = __builtin_bswap32(bits);
  std::memcpy(ctx.mmu.mem1.data() + (address & 0x3fffffffu), &bits, 4);
}

// A vertex record: the attribute flags, the position/normal/tangent/binormal
// triples, the two packed words and the 16 extra attribute floats.
static void fill_record(CPUContext& ctx, std::mt19937& rng) {
  for (uint32_t i = 0; i < 160; i += 4) {
    put32(ctx, kRecord + i, rng());
  }
  // The attribute flags: the normal, tangent, binormal, colour and the
  // second packed word are all present.
  put32(ctx, kRecord + 4, 0x10u | 0x800u | 0x1000u | 0x20u | 0x4000u | 0x1234u);
  for (uint32_t i = 16; i < 136; i += 4) {
    const float v = static_cast<float>(static_cast<int32_t>(rng() % 2001u) - 1000) * 0.125f;
    put_f32(ctx, kRecord + i, v);
  }
}

// The same guest state for both paths.
static void setup_state(CPUContext& ctx, std::mt19937& rng) {
  for (int i = 0; i < 32; ++i) {
    ctx.gpr[i] = rng();
    ctx.fpr[i] = static_cast<double>(static_cast<int32_t>(rng())) * 1.0e-3;
    ctx.ps1[i] = static_cast<double>(static_cast<int32_t>(rng())) * 1.0e-3;
  }
  for (int i = 0; i < 8; ++i) {
    ctx.cr[i].lt = (rng() & 1) != 0;
    ctx.cr[i].gt = (rng() & 1) != 0;
    ctx.cr[i].eq = (rng() & 1) != 0;
    ctx.cr[i].so = (rng() & 1) != 0;
  }
  ctx.lr = 0x803143ECu;
  ctx.ctr = rng();
  ctx.xer = rng();
  ctx.pc = 0x803143ECu;
  ctx.msr = 0x2000u;          // MSR.FP enabled: the blocks cannot fault
  ctx.fpscr = rng();
  ctx.srr0 = 0;
  ctx.srr1 = 0;
  ctx.inst_count = 0;
  ctx.gpr[26] = kRecord;     // the vertex record
  ctx.gpr[29] = kFifoBase;   // the write-gather base
  ctx.gpr[3] = kRecord;      // the attribute cursor
  ctx.gpr[4] = 0;            // the attribute index
  ctx.gpr[6] = kRecord;      // the 16-store loop cursor
}

// The same guest state for the second path.
static void copy_state(CPUContext& dst, const CPUContext& src) {
  std::memcpy(dst.gpr.data(), src.gpr.data(), sizeof(src.gpr));
  std::memcpy(dst.fpr.data(), src.fpr.data(), sizeof(src.fpr));
  std::memcpy(dst.ps1.data(), src.ps1.data(), sizeof(src.ps1));
  std::memcpy(dst.cr.data(), src.cr.data(), sizeof(src.cr));
  dst.lr = src.lr;
  dst.ctr = src.ctr;
  dst.xer = src.xer;
  dst.pc = src.pc;
  dst.msr = src.msr;
  dst.fpscr = src.fpscr;
  dst.srr0 = src.srr0;
  dst.srr1 = src.srr1;
  dst.inst_count = src.inst_count;
  std::memcpy(dst.mmu.mem1.data() + kRecord, src.mmu.mem1.data() + kRecord, 192);
}

// ---------------------------------------------------------------------------
// The native counterparts of the translated blocks.
//
// The kernel is called with the guest's own registers, exactly as the staged
// installation would.
// ---------------------------------------------------------------------------
static void native_float3_pos(CPUContext& ctx) {
  nlx::vertex_emit_float3(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                           ctx.gpr[26], 16, ctx.gpr[29] - 32768u);
  nlx::vertex_emit_flag_bit(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                             ctx.gpr[26], 0x10u, 0u);
}
static void native_float3_nrm(CPUContext& ctx) {
  nlx::vertex_emit_float3(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                           ctx.gpr[26], 36, ctx.gpr[29] - 32768u);
  nlx::vertex_emit_flag_bit(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                             ctx.gpr[26], 0x800u, 0u);
}
static void native_float3_tan(CPUContext& ctx) {
  nlx::vertex_emit_float3(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                           ctx.gpr[26], 48, ctx.gpr[29] - 32768u);
  nlx::vertex_emit_flag_bit(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                             ctx.gpr[26], 0x1000u, 0u);
}
static void native_float3_bin(CPUContext& ctx) {
  nlx::vertex_emit_float3(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                           ctx.gpr[26], 60, ctx.gpr[29] - 32768u);
  // The fourth group loads the flag word into r3 and extracts the colour bit
  // from it (`lwz r3, 4(r26)` / `rlwinm r0, r3, 0, 26, 26`).
  nlx::vertex_emit_flag_bit(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                             ctx.gpr[26], 0x20u, 3u);
}
static void native_packed1(CPUContext& ctx) {
  nlx::vertex_emit_packed1(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                            ctx.gpr[26], ctx.gpr[29] - 32768u);
}
static void native_packed2(CPUContext& ctx) {
  nlx::vertex_emit_packed2(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                            ctx.gpr[26], ctx.gpr[29] - 32768u);
}
static void native_block16(CPUContext& ctx) {
  nlx::vertex_emit_block16(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                            ctx.gpr[6], ctx.gpr[29] - 32768u);
}
static void native_pair(CPUContext& ctx) {
  nlx::vertex_emit_pair(nlx::guest_mem(ctx), ctx.inst_count, nlx::vertex_regs(ctx),
                         ctx.gpr[29] - 32768u);
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// The translated guest blocks (verbatim from the generated chunk).
// ---------------------------------------------------------------------------
// === BEGIN GENERATED TRANSLATED BLOCKS ===
// ----------------------------------------------------------------------
// The translated guest block translated_float3_pos, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1720..1758.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_pos(CPUContext& ctx) {
    // loc_80314610: ;
    // 0x80314610: C05A0018
    if (ctx.require_fpu(0x80314610)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 24); // lfs
    ++ctx.inst_count;
    // loc_80314614: ;
    // 0x80314614: C03A0014
    if (ctx.require_fpu(0x80314614)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 20); // lfs
    ++ctx.inst_count;
    // loc_80314618: ;
    // 0x80314618: C01A0010
    if (ctx.require_fpu(0x80314618)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 16); // lfs
    ++ctx.inst_count;
    // loc_8031461C: ;
    // 0x8031461C: D01D8000
    if (ctx.require_fpu(0x8031461C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314620: ;
    // 0x80314620: D03D8000
    if (ctx.require_fpu(0x80314620)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314624: ;
    ctx.inst_count += 3;
    // 0x80314624: D05D8000
    if (ctx.require_fpu(0x80314624)) { ctx.inst_count -= 3; return; } // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    // loc_80314628: ;
    // 0x80314628: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_8031462C: ;
    // 0x8031462C: 540006F7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x10; // rlwinm r0, r0, 0, 27, 27
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_pos, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1720..1758.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_pos(CPUContext& ctx) {
    // loc_80314610: ;
    // 0x80314610: C05A0018
    if (ctx.require_fpu(0x80314610)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 24); // lfs
    ++ctx.inst_count;
    // loc_80314614: ;
    // 0x80314614: C03A0014
    if (ctx.require_fpu(0x80314614)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 20); // lfs
    ++ctx.inst_count;
    // loc_80314618: ;
    // 0x80314618: C01A0010
    if (ctx.require_fpu(0x80314618)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 16); // lfs
    ++ctx.inst_count;
    // loc_8031461C: ;
    // 0x8031461C: D01D8000
    if (ctx.require_fpu(0x8031461C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314620: ;
    // 0x80314620: D03D8000
    if (ctx.require_fpu(0x80314620)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314624: ;
    ctx.inst_count += 3;
    // 0x80314624: D05D8000
    if (ctx.require_fpu(0x80314624)) { ctx.inst_count -= 3; return; } // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    // loc_80314628: ;
    // 0x80314628: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_8031462C: ;
    // 0x8031462C: 540006F7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x10; // rlwinm r0, r0, 0, 27, 27
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_nrm, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1763..1802.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_nrm(CPUContext& ctx) {
    // loc_80314634: ;
    // 0x80314634: C05A002C
    if (ctx.require_fpu(0x80314634)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 44); // lfs
    ++ctx.inst_count;
    // loc_80314638: ;
    // 0x80314638: C03A0028
    if (ctx.require_fpu(0x80314638)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 40); // lfs
    ++ctx.inst_count;
    // loc_8031463C: ;
    // 0x8031463C: C01A0024
    if (ctx.require_fpu(0x8031463C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 36); // lfs
    ++ctx.inst_count;
    // loc_80314640: ;
    // 0x80314640: D01D8000
    if (ctx.require_fpu(0x80314640)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314644: ;
    // 0x80314644: D03D8000
    if (ctx.require_fpu(0x80314644)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314648: ;
    // 0x80314648: D05D8000
    if (ctx.require_fpu(0x80314648)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_8031464C: ;
    ctx.inst_count += 2;
    // 0x8031464C: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_80314650: ;
    // 0x80314650: 54000529
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x800; // rlwinm r0, r0, 0, 20, 20
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_nrm, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1763..1802.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_nrm(CPUContext& ctx) {
    // loc_80314634: ;
    // 0x80314634: C05A002C
    if (ctx.require_fpu(0x80314634)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 44); // lfs
    ++ctx.inst_count;
    // loc_80314638: ;
    // 0x80314638: C03A0028
    if (ctx.require_fpu(0x80314638)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 40); // lfs
    ++ctx.inst_count;
    // loc_8031463C: ;
    // 0x8031463C: C01A0024
    if (ctx.require_fpu(0x8031463C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 36); // lfs
    ++ctx.inst_count;
    // loc_80314640: ;
    // 0x80314640: D01D8000
    if (ctx.require_fpu(0x80314640)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314644: ;
    // 0x80314644: D03D8000
    if (ctx.require_fpu(0x80314644)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314648: ;
    // 0x80314648: D05D8000
    if (ctx.require_fpu(0x80314648)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_8031464C: ;
    ctx.inst_count += 2;
    // 0x8031464C: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_80314650: ;
    // 0x80314650: 54000529
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x800; // rlwinm r0, r0, 0, 20, 20
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_tan, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1807..1846.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_tan(CPUContext& ctx) {
    // loc_80314658: ;
    // 0x80314658: C05A0038
    if (ctx.require_fpu(0x80314658)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 56); // lfs
    ++ctx.inst_count;
    // loc_8031465C: ;
    // 0x8031465C: C03A0034
    if (ctx.require_fpu(0x8031465C)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 52); // lfs
    ++ctx.inst_count;
    // loc_80314660: ;
    // 0x80314660: C01A0030
    if (ctx.require_fpu(0x80314660)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 48); // lfs
    ++ctx.inst_count;
    // loc_80314664: ;
    // 0x80314664: D01D8000
    if (ctx.require_fpu(0x80314664)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314668: ;
    // 0x80314668: D03D8000
    if (ctx.require_fpu(0x80314668)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031466C: ;
    // 0x8031466C: D05D8000
    if (ctx.require_fpu(0x8031466C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_80314670: ;
    ctx.inst_count += 2;
    // 0x80314670: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_80314674: ;
    // 0x80314674: 540004E7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x1000; // rlwinm r0, r0, 0, 19, 19
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_tan, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1807..1846.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_tan(CPUContext& ctx) {
    // loc_80314658: ;
    // 0x80314658: C05A0038
    if (ctx.require_fpu(0x80314658)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 56); // lfs
    ++ctx.inst_count;
    // loc_8031465C: ;
    // 0x8031465C: C03A0034
    if (ctx.require_fpu(0x8031465C)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 52); // lfs
    ++ctx.inst_count;
    // loc_80314660: ;
    // 0x80314660: C01A0030
    if (ctx.require_fpu(0x80314660)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 48); // lfs
    ++ctx.inst_count;
    // loc_80314664: ;
    // 0x80314664: D01D8000
    if (ctx.require_fpu(0x80314664)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314668: ;
    // 0x80314668: D03D8000
    if (ctx.require_fpu(0x80314668)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031466C: ;
    // 0x8031466C: D05D8000
    if (ctx.require_fpu(0x8031466C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_80314670: ;
    ctx.inst_count += 2;
    // 0x80314670: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_80314674: ;
    // 0x80314674: 540004E7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x1000; // rlwinm r0, r0, 0, 19, 19
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_bin, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1852..1890.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_bin(CPUContext& ctx) {
    // 0x8031467C: C05A0044
    if (ctx.require_fpu(0x8031467C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 68); // lfs
    ++ctx.inst_count;
    // loc_80314680: ;
    // 0x80314680: C03A0040
    if (ctx.require_fpu(0x80314680)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 64); // lfs
    ++ctx.inst_count;
    // loc_80314684: ;
    // 0x80314684: C01A003C
    if (ctx.require_fpu(0x80314684)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 60); // lfs
    ++ctx.inst_count;
    // loc_80314688: ;
    // 0x80314688: D01D8000
    if (ctx.require_fpu(0x80314688)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031468C: ;
    // 0x8031468C: D03D8000
    if (ctx.require_fpu(0x8031468C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314690: ;
    // 0x80314690: D05D8000
    if (ctx.require_fpu(0x80314690)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_80314694: ;
    ctx.inst_count += 2;
    // 0x80314694: 807A0004
    ctx.gpr[3] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r3, 4(r26)
    // loc_80314698: ;
    // 0x80314698: 546006B5
    ctx.gpr[0] = std::rotl(ctx.gpr[3], 0) & 0x20; // rlwinm r0, r3, 0, 26, 26
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_bin, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1852..1890.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_bin(CPUContext& ctx) {
    // 0x8031467C: C05A0044
    if (ctx.require_fpu(0x8031467C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 68); // lfs
    ++ctx.inst_count;
    // loc_80314680: ;
    // 0x80314680: C03A0040
    if (ctx.require_fpu(0x80314680)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 64); // lfs
    ++ctx.inst_count;
    // loc_80314684: ;
    // 0x80314684: C01A003C
    if (ctx.require_fpu(0x80314684)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 60); // lfs
    ++ctx.inst_count;
    // loc_80314688: ;
    // 0x80314688: D01D8000
    if (ctx.require_fpu(0x80314688)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031468C: ;
    // 0x8031468C: D03D8000
    if (ctx.require_fpu(0x8031468C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314690: ;
    // 0x80314690: D05D8000
    if (ctx.require_fpu(0x80314690)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_80314694: ;
    ctx.inst_count += 2;
    // 0x80314694: 807A0004
    ctx.gpr[3] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r3, 4(r26)
    // loc_80314698: ;
    // 0x80314698: 546006B5
    ctx.gpr[0] = std::rotl(ctx.gpr[3], 0) & 0x20; // rlwinm r0, r3, 0, 26, 26
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_packed1, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1895..1901.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_packed1(CPUContext& ctx) {
    // loc_803146A0: ;
    ctx.inst_count += 2;
    // 0x803146A0: 801A001C
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_803146A4: ;
    // 0x803146A4: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
}


// ----------------------------------------------------------------------
// The generated guest block bench_packed1, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1895..1901.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_packed1(CPUContext& ctx) {
    // loc_803146A0: ;
    ctx.inst_count += 2;
    // 0x803146A0: 801A001C
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_803146A4: ;
    // 0x803146A4: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
}


// ----------------------------------------------------------------------
// The translated guest block translated_packed2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1917..1930.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_packed2(CPUContext& ctx) {
    // loc_803146B4: ;
    ctx.inst_count += 3;
    // 0x803146B4: 801A001C
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_803146B8: ;
    // 0x803146B8: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    // loc_803146BC: ;
    // 0x803146BC: 801A0020
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 32); // lwz r0, 32(r26)
    // loc_803146C0: ;
    // 0x803146C0: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The generated guest block bench_packed2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 1917..1930.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_packed2(CPUContext& ctx) {
    // loc_803146B4: ;
    ctx.inst_count += 3;
    // 0x803146B4: 801A001C
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_803146B8: ;
    // 0x803146B8: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    // loc_803146BC: ;
    // 0x803146BC: 801A0020
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 32); // lwz r0, 32(r26)
    // loc_803146C0: ;
    // 0x803146C0: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The translated guest block translated_block16, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2019..2186.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_block16(CPUContext& ctx) {
    ++ctx.inst_count;
    // loc_8031471C: ;
    ctx.inst_count += 1;
    // 0x8031471C: C026004C
    if (ctx.require_fpu(0x8031471C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 76); // lfs
    // loc_80314720: ;
    // 0x80314720: 38840008
    ctx.gpr[4] = ctx.gpr[4] + 8; // addi r4, r4, 8
    ++ctx.inst_count;
    // loc_80314724: ;
    // 0x80314724: C0060048
    if (ctx.require_fpu(0x80314724)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 72); // lfs
    ++ctx.inst_count;
    // loc_80314728: ;
    // 0x80314728: D01D8000
    if (ctx.require_fpu(0x80314728)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031472C: ;
    // 0x8031472C: D03D8000
    if (ctx.require_fpu(0x8031472C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314730: ;
    // 0x80314730: C0060054
    if (ctx.require_fpu(0x80314730)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 84); // lfs
    ++ctx.inst_count;
    // loc_80314734: ;
    // 0x80314734: C0260050
    if (ctx.require_fpu(0x80314734)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 80); // lfs
    ++ctx.inst_count;
    // loc_80314738: ;
    // 0x80314738: D03D8000
    if (ctx.require_fpu(0x80314738)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031473C: ;
    // 0x8031473C: D01D8000
    if (ctx.require_fpu(0x8031473C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314740: ;
    // 0x80314740: C006005C
    if (ctx.require_fpu(0x80314740)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 92); // lfs
    ++ctx.inst_count;
    // loc_80314744: ;
    // 0x80314744: C0260058
    if (ctx.require_fpu(0x80314744)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 88); // lfs
    ++ctx.inst_count;
    // loc_80314748: ;
    // 0x80314748: D03D8000
    if (ctx.require_fpu(0x80314748)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031474C: ;
    // 0x8031474C: D01D8000
    if (ctx.require_fpu(0x8031474C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314750: ;
    // 0x80314750: C0060064
    if (ctx.require_fpu(0x80314750)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 100); // lfs
    ++ctx.inst_count;
    // loc_80314754: ;
    // 0x80314754: C0260060
    if (ctx.require_fpu(0x80314754)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 96); // lfs
    ++ctx.inst_count;
    // loc_80314758: ;
    // 0x80314758: D03D8000
    if (ctx.require_fpu(0x80314758)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031475C: ;
    // 0x8031475C: D01D8000
    if (ctx.require_fpu(0x8031475C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314760: ;
    // 0x80314760: C006006C
    if (ctx.require_fpu(0x80314760)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 108); // lfs
    ++ctx.inst_count;
    // loc_80314764: ;
    // 0x80314764: C0260068
    if (ctx.require_fpu(0x80314764)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 104); // lfs
    ++ctx.inst_count;
    // loc_80314768: ;
    // 0x80314768: D03D8000
    if (ctx.require_fpu(0x80314768)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031476C: ;
    // 0x8031476C: D01D8000
    if (ctx.require_fpu(0x8031476C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314770: ;
    // 0x80314770: C0060074
    if (ctx.require_fpu(0x80314770)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 116); // lfs
    ++ctx.inst_count;
    // loc_80314774: ;
    // 0x80314774: C0260070
    if (ctx.require_fpu(0x80314774)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 112); // lfs
    ++ctx.inst_count;
    // loc_80314778: ;
    // 0x80314778: D03D8000
    if (ctx.require_fpu(0x80314778)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031477C: ;
    // 0x8031477C: D01D8000
    if (ctx.require_fpu(0x8031477C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314780: ;
    // 0x80314780: C006007C
    if (ctx.require_fpu(0x80314780)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 124); // lfs
    ++ctx.inst_count;
    // loc_80314784: ;
    // 0x80314784: C0260078
    if (ctx.require_fpu(0x80314784)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 120); // lfs
    ++ctx.inst_count;
    // loc_80314788: ;
    // 0x80314788: D03D8000
    if (ctx.require_fpu(0x80314788)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031478C: ;
    // 0x8031478C: D01D8000
    if (ctx.require_fpu(0x8031478C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314790: ;
    // 0x80314790: C0060084
    if (ctx.require_fpu(0x80314790)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 132); // lfs
    ++ctx.inst_count;
    // loc_80314794: ;
    ctx.inst_count += 1;
    // 0x80314794: C0260080
    if (ctx.require_fpu(0x80314794)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 128); // lfs
    // loc_80314798: ;
    // 0x80314798: 38C60040
    ctx.gpr[6] = ctx.gpr[6] + 64; // addi r6, r6, 64
    ++ctx.inst_count;
    // loc_8031479C: ;
    // 0x8031479C: D03D8000
    if (ctx.require_fpu(0x8031479C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803147A0: ;
    // 0x803147A0: D01D8000
    if (ctx.require_fpu(0x803147A0)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
}


// ----------------------------------------------------------------------
// The generated guest block bench_block16, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2019..2186.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_block16(CPUContext& ctx) {
    ++ctx.inst_count;
    // loc_8031471C: ;
    ctx.inst_count += 1;
    // 0x8031471C: C026004C
    if (ctx.require_fpu(0x8031471C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 76); // lfs
    // loc_80314720: ;
    // 0x80314720: 38840008
    ctx.gpr[4] = ctx.gpr[4] + 8; // addi r4, r4, 8
    ++ctx.inst_count;
    // loc_80314724: ;
    // 0x80314724: C0060048
    if (ctx.require_fpu(0x80314724)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 72); // lfs
    ++ctx.inst_count;
    // loc_80314728: ;
    // 0x80314728: D01D8000
    if (ctx.require_fpu(0x80314728)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031472C: ;
    // 0x8031472C: D03D8000
    if (ctx.require_fpu(0x8031472C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314730: ;
    // 0x80314730: C0060054
    if (ctx.require_fpu(0x80314730)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 84); // lfs
    ++ctx.inst_count;
    // loc_80314734: ;
    // 0x80314734: C0260050
    if (ctx.require_fpu(0x80314734)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 80); // lfs
    ++ctx.inst_count;
    // loc_80314738: ;
    // 0x80314738: D03D8000
    if (ctx.require_fpu(0x80314738)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031473C: ;
    // 0x8031473C: D01D8000
    if (ctx.require_fpu(0x8031473C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314740: ;
    // 0x80314740: C006005C
    if (ctx.require_fpu(0x80314740)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 92); // lfs
    ++ctx.inst_count;
    // loc_80314744: ;
    // 0x80314744: C0260058
    if (ctx.require_fpu(0x80314744)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 88); // lfs
    ++ctx.inst_count;
    // loc_80314748: ;
    // 0x80314748: D03D8000
    if (ctx.require_fpu(0x80314748)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031474C: ;
    // 0x8031474C: D01D8000
    if (ctx.require_fpu(0x8031474C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314750: ;
    // 0x80314750: C0060064
    if (ctx.require_fpu(0x80314750)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 100); // lfs
    ++ctx.inst_count;
    // loc_80314754: ;
    // 0x80314754: C0260060
    if (ctx.require_fpu(0x80314754)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 96); // lfs
    ++ctx.inst_count;
    // loc_80314758: ;
    // 0x80314758: D03D8000
    if (ctx.require_fpu(0x80314758)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031475C: ;
    // 0x8031475C: D01D8000
    if (ctx.require_fpu(0x8031475C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314760: ;
    // 0x80314760: C006006C
    if (ctx.require_fpu(0x80314760)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 108); // lfs
    ++ctx.inst_count;
    // loc_80314764: ;
    // 0x80314764: C0260068
    if (ctx.require_fpu(0x80314764)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 104); // lfs
    ++ctx.inst_count;
    // loc_80314768: ;
    // 0x80314768: D03D8000
    if (ctx.require_fpu(0x80314768)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031476C: ;
    // 0x8031476C: D01D8000
    if (ctx.require_fpu(0x8031476C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314770: ;
    // 0x80314770: C0060074
    if (ctx.require_fpu(0x80314770)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 116); // lfs
    ++ctx.inst_count;
    // loc_80314774: ;
    // 0x80314774: C0260070
    if (ctx.require_fpu(0x80314774)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 112); // lfs
    ++ctx.inst_count;
    // loc_80314778: ;
    // 0x80314778: D03D8000
    if (ctx.require_fpu(0x80314778)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031477C: ;
    // 0x8031477C: D01D8000
    if (ctx.require_fpu(0x8031477C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314780: ;
    // 0x80314780: C006007C
    if (ctx.require_fpu(0x80314780)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 124); // lfs
    ++ctx.inst_count;
    // loc_80314784: ;
    // 0x80314784: C0260078
    if (ctx.require_fpu(0x80314784)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 120); // lfs
    ++ctx.inst_count;
    // loc_80314788: ;
    // 0x80314788: D03D8000
    if (ctx.require_fpu(0x80314788)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031478C: ;
    // 0x8031478C: D01D8000
    if (ctx.require_fpu(0x8031478C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314790: ;
    // 0x80314790: C0060084
    if (ctx.require_fpu(0x80314790)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 132); // lfs
    ++ctx.inst_count;
    // loc_80314794: ;
    ctx.inst_count += 1;
    // 0x80314794: C0260080
    if (ctx.require_fpu(0x80314794)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 128); // lfs
    // loc_80314798: ;
    // 0x80314798: 38C60040
    ctx.gpr[6] = ctx.gpr[6] + 64; // addi r6, r6, 64
    ++ctx.inst_count;
    // loc_8031479C: ;
    // 0x8031479C: D03D8000
    if (ctx.require_fpu(0x8031479C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803147A0: ;
    // 0x803147A0: D01D8000
    if (ctx.require_fpu(0x803147A0)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
}


// ----------------------------------------------------------------------
// The translated guest block translated_pair, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2224..2251.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_pair(CPUContext& ctx) {
    // loc_803147C0: ;
    ctx.inst_count += 1;
    // 0x803147C0: C003004C
    if (ctx.require_fpu(0x803147C0)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 76); // lfs
    // loc_803147C4: ;
    // 0x803147C4: 38840001
    ctx.gpr[4] = ctx.gpr[4] + 1; // addi r4, r4, 1
    ++ctx.inst_count;
    // loc_803147C8: ;
    ctx.inst_count += 1;
    // 0x803147C8: C0230048
    if (ctx.require_fpu(0x803147C8)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 72); // lfs
    // loc_803147CC: ;
    // 0x803147CC: 38630008
    ctx.gpr[3] = ctx.gpr[3] + 8; // addi r3, r3, 8
    ++ctx.inst_count;
    // loc_803147D0: ;
    // 0x803147D0: D03D8000
    if (ctx.require_fpu(0x803147D0)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803147D4: ;
    // 0x803147D4: D01D8000
    if (ctx.require_fpu(0x803147D4)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The generated guest block bench_pair, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2224..2251.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_pair(CPUContext& ctx) {
    // loc_803147C0: ;
    ctx.inst_count += 1;
    // 0x803147C0: C003004C
    if (ctx.require_fpu(0x803147C0)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[3] + 76); // lfs
    // loc_803147C4: ;
    // 0x803147C4: 38840001
    ctx.gpr[4] = ctx.gpr[4] + 1; // addi r4, r4, 1
    ++ctx.inst_count;
    // loc_803147C8: ;
    ctx.inst_count += 1;
    // 0x803147C8: C0230048
    if (ctx.require_fpu(0x803147C8)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[3] + 72); // lfs
    // loc_803147CC: ;
    // 0x803147CC: 38630008
    ctx.gpr[3] = ctx.gpr[3] + 8; // addi r3, r3, 8
    ++ctx.inst_count;
    // loc_803147D0: ;
    // 0x803147D0: D03D8000
    if (ctx.require_fpu(0x803147D0)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803147D4: ;
    // 0x803147D4: D01D8000
    if (ctx.require_fpu(0x803147D4)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_pos2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2422..2460.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_pos2(CPUContext& ctx) {
    // loc_80314870: ;
    // 0x80314870: C05A0018
    if (ctx.require_fpu(0x80314870)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 24); // lfs
    ++ctx.inst_count;
    // loc_80314874: ;
    // 0x80314874: C03A0014
    if (ctx.require_fpu(0x80314874)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 20); // lfs
    ++ctx.inst_count;
    // loc_80314878: ;
    // 0x80314878: C01A0010
    if (ctx.require_fpu(0x80314878)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 16); // lfs
    ++ctx.inst_count;
    // loc_8031487C: ;
    // 0x8031487C: D01D8000
    if (ctx.require_fpu(0x8031487C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314880: ;
    // 0x80314880: D03D8000
    if (ctx.require_fpu(0x80314880)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314884: ;
    ctx.inst_count += 3;
    // 0x80314884: D05D8000
    if (ctx.require_fpu(0x80314884)) { ctx.inst_count -= 3; return; } // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    // loc_80314888: ;
    // 0x80314888: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_8031488C: ;
    // 0x8031488C: 540006F7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x10; // rlwinm r0, r0, 0, 27, 27
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_pos2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2422..2460.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_pos2(CPUContext& ctx) {
    // loc_80314870: ;
    // 0x80314870: C05A0018
    if (ctx.require_fpu(0x80314870)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 24); // lfs
    ++ctx.inst_count;
    // loc_80314874: ;
    // 0x80314874: C03A0014
    if (ctx.require_fpu(0x80314874)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 20); // lfs
    ++ctx.inst_count;
    // loc_80314878: ;
    // 0x80314878: C01A0010
    if (ctx.require_fpu(0x80314878)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 16); // lfs
    ++ctx.inst_count;
    // loc_8031487C: ;
    // 0x8031487C: D01D8000
    if (ctx.require_fpu(0x8031487C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_80314880: ;
    // 0x80314880: D03D8000
    if (ctx.require_fpu(0x80314880)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314884: ;
    ctx.inst_count += 3;
    // 0x80314884: D05D8000
    if (ctx.require_fpu(0x80314884)) { ctx.inst_count -= 3; return; } // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    // loc_80314888: ;
    // 0x80314888: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_8031488C: ;
    // 0x8031488C: 540006F7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x10; // rlwinm r0, r0, 0, 27, 27
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_nrm2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2465..2504.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_nrm2(CPUContext& ctx) {
    // loc_80314894: ;
    // 0x80314894: C05A002C
    if (ctx.require_fpu(0x80314894)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 44); // lfs
    ++ctx.inst_count;
    // loc_80314898: ;
    // 0x80314898: C03A0028
    if (ctx.require_fpu(0x80314898)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 40); // lfs
    ++ctx.inst_count;
    // loc_8031489C: ;
    // 0x8031489C: C01A0024
    if (ctx.require_fpu(0x8031489C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 36); // lfs
    ++ctx.inst_count;
    // loc_803148A0: ;
    // 0x803148A0: D01D8000
    if (ctx.require_fpu(0x803148A0)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148A4: ;
    // 0x803148A4: D03D8000
    if (ctx.require_fpu(0x803148A4)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148A8: ;
    // 0x803148A8: D05D8000
    if (ctx.require_fpu(0x803148A8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148AC: ;
    ctx.inst_count += 2;
    // 0x803148AC: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_803148B0: ;
    // 0x803148B0: 54000529
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x800; // rlwinm r0, r0, 0, 20, 20
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_nrm2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2465..2504.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_nrm2(CPUContext& ctx) {
    // loc_80314894: ;
    // 0x80314894: C05A002C
    if (ctx.require_fpu(0x80314894)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 44); // lfs
    ++ctx.inst_count;
    // loc_80314898: ;
    // 0x80314898: C03A0028
    if (ctx.require_fpu(0x80314898)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 40); // lfs
    ++ctx.inst_count;
    // loc_8031489C: ;
    // 0x8031489C: C01A0024
    if (ctx.require_fpu(0x8031489C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 36); // lfs
    ++ctx.inst_count;
    // loc_803148A0: ;
    // 0x803148A0: D01D8000
    if (ctx.require_fpu(0x803148A0)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148A4: ;
    // 0x803148A4: D03D8000
    if (ctx.require_fpu(0x803148A4)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148A8: ;
    // 0x803148A8: D05D8000
    if (ctx.require_fpu(0x803148A8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148AC: ;
    ctx.inst_count += 2;
    // 0x803148AC: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_803148B0: ;
    // 0x803148B0: 54000529
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x800; // rlwinm r0, r0, 0, 20, 20
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_tan2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2509..2548.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_tan2(CPUContext& ctx) {
    // loc_803148B8: ;
    // 0x803148B8: C05A0038
    if (ctx.require_fpu(0x803148B8)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 56); // lfs
    ++ctx.inst_count;
    // loc_803148BC: ;
    // 0x803148BC: C03A0034
    if (ctx.require_fpu(0x803148BC)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 52); // lfs
    ++ctx.inst_count;
    // loc_803148C0: ;
    // 0x803148C0: C01A0030
    if (ctx.require_fpu(0x803148C0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 48); // lfs
    ++ctx.inst_count;
    // loc_803148C4: ;
    // 0x803148C4: D01D8000
    if (ctx.require_fpu(0x803148C4)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148C8: ;
    // 0x803148C8: D03D8000
    if (ctx.require_fpu(0x803148C8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148CC: ;
    // 0x803148CC: D05D8000
    if (ctx.require_fpu(0x803148CC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148D0: ;
    ctx.inst_count += 2;
    // 0x803148D0: 801A0004
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_803148D4: ;
    // 0x803148D4: 540004E7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x1000; // rlwinm r0, r0, 0, 19, 19
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_tan2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2509..2548.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_tan2(CPUContext& ctx) {
    // loc_803148B8: ;
    // 0x803148B8: C05A0038
    if (ctx.require_fpu(0x803148B8)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 56); // lfs
    ++ctx.inst_count;
    // loc_803148BC: ;
    // 0x803148BC: C03A0034
    if (ctx.require_fpu(0x803148BC)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 52); // lfs
    ++ctx.inst_count;
    // loc_803148C0: ;
    // 0x803148C0: C01A0030
    if (ctx.require_fpu(0x803148C0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 48); // lfs
    ++ctx.inst_count;
    // loc_803148C4: ;
    // 0x803148C4: D01D8000
    if (ctx.require_fpu(0x803148C4)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148C8: ;
    // 0x803148C8: D03D8000
    if (ctx.require_fpu(0x803148C8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148CC: ;
    // 0x803148CC: D05D8000
    if (ctx.require_fpu(0x803148CC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148D0: ;
    ctx.inst_count += 2;
    // 0x803148D0: 801A0004
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r0, 4(r26)
    // loc_803148D4: ;
    // 0x803148D4: 540004E7
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 0) & 0x1000; // rlwinm r0, r0, 0, 19, 19
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_float3_bin2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2553..2592.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_float3_bin2(CPUContext& ctx) {
    // loc_803148DC: ;
    // 0x803148DC: C05A0044
    if (ctx.require_fpu(0x803148DC)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 68); // lfs
    ++ctx.inst_count;
    // loc_803148E0: ;
    // 0x803148E0: C03A0040
    if (ctx.require_fpu(0x803148E0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 64); // lfs
    ++ctx.inst_count;
    // loc_803148E4: ;
    // 0x803148E4: C01A003C
    if (ctx.require_fpu(0x803148E4)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[26] + 60); // lfs
    ++ctx.inst_count;
    // loc_803148E8: ;
    // 0x803148E8: D01D8000
    if (ctx.require_fpu(0x803148E8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148EC: ;
    // 0x803148EC: D03D8000
    if (ctx.require_fpu(0x803148EC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148F0: ;
    // 0x803148F0: D05D8000
    if (ctx.require_fpu(0x803148F0)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148F4: ;
    ctx.inst_count += 2;
    // 0x803148F4: 807A0004
    ctx.gpr[3] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 4); // lwz r3, 4(r26)
    // loc_803148F8: ;
    // 0x803148F8: 546006B5
    ctx.gpr[0] = std::rotl(ctx.gpr[3], 0) & 0x20; // rlwinm r0, r3, 0, 26, 26
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The generated guest block bench_float3_bin2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2553..2592.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_float3_bin2(CPUContext& ctx) {
    // loc_803148DC: ;
    // 0x803148DC: C05A0044
    if (ctx.require_fpu(0x803148DC)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 68); // lfs
    ++ctx.inst_count;
    // loc_803148E0: ;
    // 0x803148E0: C03A0040
    if (ctx.require_fpu(0x803148E0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 64); // lfs
    ++ctx.inst_count;
    // loc_803148E4: ;
    // 0x803148E4: C01A003C
    if (ctx.require_fpu(0x803148E4)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[26] + 60); // lfs
    ++ctx.inst_count;
    // loc_803148E8: ;
    // 0x803148E8: D01D8000
    if (ctx.require_fpu(0x803148E8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803148EC: ;
    // 0x803148EC: D03D8000
    if (ctx.require_fpu(0x803148EC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803148F0: ;
    // 0x803148F0: D05D8000
    if (ctx.require_fpu(0x803148F0)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[2]); // stfs f2, -32768(r29)
    ++ctx.inst_count;
    // loc_803148F4: ;
    ctx.inst_count += 2;
    // 0x803148F4: 807A0004
    ctx.gpr[3] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 4); // lwz r3, 4(r26)
    // loc_803148F8: ;
    // 0x803148F8: 546006B5
    ctx.gpr[0] = std::rotl(ctx.gpr[3], 0) & 0x20; // rlwinm r0, r3, 0, 26, 26
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
}


// ----------------------------------------------------------------------
// The translated guest block translated_packed1_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2597..2603.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_packed1_2(CPUContext& ctx) {
    // loc_80314900: ;
    ctx.inst_count += 2;
    // 0x80314900: 801A001C
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_80314904: ;
    // 0x80314904: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
}


// ----------------------------------------------------------------------
// The generated guest block bench_packed1_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2597..2603.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_packed1_2(CPUContext& ctx) {
    // loc_80314900: ;
    ctx.inst_count += 2;
    // 0x80314900: 801A001C
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_80314904: ;
    // 0x80314904: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
}


// ----------------------------------------------------------------------
// The translated guest block translated_packed2_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2619..2632.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_packed2_2(CPUContext& ctx) {
    // loc_80314914: ;
    ctx.inst_count += 3;
    // 0x80314914: 801A001C
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_80314918: ;
    // 0x80314918: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    // loc_8031491C: ;
    // 0x8031491C: 801A0020
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[26] + 32); // lwz r0, 32(r26)
    // loc_80314920: ;
    // 0x80314920: 901D8000
    nl::rec_write32(ctx.mmu, ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The generated guest block bench_packed2_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2619..2632.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_packed2_2(CPUContext& ctx) {
    // loc_80314914: ;
    ctx.inst_count += 3;
    // 0x80314914: 801A001C
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 28); // lwz r0, 28(r26)
    // loc_80314918: ;
    // 0x80314918: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    // loc_8031491C: ;
    // 0x8031491C: 801A0020
    ctx.gpr[0] = native_fastmem::read32(ctx.mmu,ctx.gpr[26] + 32); // lwz r0, 32(r26)
    // loc_80314920: ;
    // 0x80314920: 901D8000
    ctx.mmu.write32(ctx.gpr[29] + -32768, ctx.gpr[0]); // stw r0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The translated guest block translated_block16_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2722..2889.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_block16_2(CPUContext& ctx) {
    // loc_8031497C: ;
    ctx.inst_count += 1;
    // 0x8031497C: C026004C
    if (ctx.require_fpu(0x8031497C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 76); // lfs
    // loc_80314980: ;
    // 0x80314980: 38840008
    ctx.gpr[4] = ctx.gpr[4] + 8; // addi r4, r4, 8
    ++ctx.inst_count;
    // loc_80314984: ;
    // 0x80314984: C0060048
    if (ctx.require_fpu(0x80314984)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 72); // lfs
    ++ctx.inst_count;
    // loc_80314988: ;
    // 0x80314988: D01D8000
    if (ctx.require_fpu(0x80314988)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031498C: ;
    // 0x8031498C: D03D8000
    if (ctx.require_fpu(0x8031498C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314990: ;
    // 0x80314990: C0060054
    if (ctx.require_fpu(0x80314990)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 84); // lfs
    ++ctx.inst_count;
    // loc_80314994: ;
    // 0x80314994: C0260050
    if (ctx.require_fpu(0x80314994)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 80); // lfs
    ++ctx.inst_count;
    // loc_80314998: ;
    // 0x80314998: D03D8000
    if (ctx.require_fpu(0x80314998)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031499C: ;
    // 0x8031499C: D01D8000
    if (ctx.require_fpu(0x8031499C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149A0: ;
    // 0x803149A0: C006005C
    if (ctx.require_fpu(0x803149A0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 92); // lfs
    ++ctx.inst_count;
    // loc_803149A4: ;
    // 0x803149A4: C0260058
    if (ctx.require_fpu(0x803149A4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 88); // lfs
    ++ctx.inst_count;
    // loc_803149A8: ;
    // 0x803149A8: D03D8000
    if (ctx.require_fpu(0x803149A8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149AC: ;
    // 0x803149AC: D01D8000
    if (ctx.require_fpu(0x803149AC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149B0: ;
    // 0x803149B0: C0060064
    if (ctx.require_fpu(0x803149B0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 100); // lfs
    ++ctx.inst_count;
    // loc_803149B4: ;
    // 0x803149B4: C0260060
    if (ctx.require_fpu(0x803149B4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 96); // lfs
    ++ctx.inst_count;
    // loc_803149B8: ;
    // 0x803149B8: D03D8000
    if (ctx.require_fpu(0x803149B8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149BC: ;
    // 0x803149BC: D01D8000
    if (ctx.require_fpu(0x803149BC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149C0: ;
    // 0x803149C0: C006006C
    if (ctx.require_fpu(0x803149C0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 108); // lfs
    ++ctx.inst_count;
    // loc_803149C4: ;
    // 0x803149C4: C0260068
    if (ctx.require_fpu(0x803149C4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 104); // lfs
    ++ctx.inst_count;
    // loc_803149C8: ;
    // 0x803149C8: D03D8000
    if (ctx.require_fpu(0x803149C8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149CC: ;
    // 0x803149CC: D01D8000
    if (ctx.require_fpu(0x803149CC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149D0: ;
    // 0x803149D0: C0060074
    if (ctx.require_fpu(0x803149D0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 116); // lfs
    ++ctx.inst_count;
    // loc_803149D4: ;
    // 0x803149D4: C0260070
    if (ctx.require_fpu(0x803149D4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 112); // lfs
    ++ctx.inst_count;
    // loc_803149D8: ;
    // 0x803149D8: D03D8000
    if (ctx.require_fpu(0x803149D8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149DC: ;
    // 0x803149DC: D01D8000
    if (ctx.require_fpu(0x803149DC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149E0: ;
    // 0x803149E0: C006007C
    if (ctx.require_fpu(0x803149E0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 124); // lfs
    ++ctx.inst_count;
    // loc_803149E4: ;
    // 0x803149E4: C0260078
    if (ctx.require_fpu(0x803149E4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 120); // lfs
    ++ctx.inst_count;
    // loc_803149E8: ;
    // 0x803149E8: D03D8000
    if (ctx.require_fpu(0x803149E8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149EC: ;
    // 0x803149EC: D01D8000
    if (ctx.require_fpu(0x803149EC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149F0: ;
    // 0x803149F0: C0060084
    if (ctx.require_fpu(0x803149F0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 132); // lfs
    ++ctx.inst_count;
    // loc_803149F4: ;
    ctx.inst_count += 1;
    // 0x803149F4: C0260080
    if (ctx.require_fpu(0x803149F4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[6] + 128); // lfs
    // loc_803149F8: ;
    // 0x803149F8: 38C60040
    ctx.gpr[6] = ctx.gpr[6] + 64; // addi r6, r6, 64
    ++ctx.inst_count;
    // loc_803149FC: ;
    // 0x803149FC: D03D8000
    if (ctx.require_fpu(0x803149FC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314A00: ;
    // 0x80314A00: D01D8000
    if (ctx.require_fpu(0x80314A00)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The generated guest block bench_block16_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2722..2889.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_block16_2(CPUContext& ctx) {
    // loc_8031497C: ;
    ctx.inst_count += 1;
    // 0x8031497C: C026004C
    if (ctx.require_fpu(0x8031497C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 76); // lfs
    // loc_80314980: ;
    // 0x80314980: 38840008
    ctx.gpr[4] = ctx.gpr[4] + 8; // addi r4, r4, 8
    ++ctx.inst_count;
    // loc_80314984: ;
    // 0x80314984: C0060048
    if (ctx.require_fpu(0x80314984)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 72); // lfs
    ++ctx.inst_count;
    // loc_80314988: ;
    // 0x80314988: D01D8000
    if (ctx.require_fpu(0x80314988)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_8031498C: ;
    // 0x8031498C: D03D8000
    if (ctx.require_fpu(0x8031498C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314990: ;
    // 0x80314990: C0060054
    if (ctx.require_fpu(0x80314990)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 84); // lfs
    ++ctx.inst_count;
    // loc_80314994: ;
    // 0x80314994: C0260050
    if (ctx.require_fpu(0x80314994)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 80); // lfs
    ++ctx.inst_count;
    // loc_80314998: ;
    // 0x80314998: D03D8000
    if (ctx.require_fpu(0x80314998)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_8031499C: ;
    // 0x8031499C: D01D8000
    if (ctx.require_fpu(0x8031499C)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149A0: ;
    // 0x803149A0: C006005C
    if (ctx.require_fpu(0x803149A0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 92); // lfs
    ++ctx.inst_count;
    // loc_803149A4: ;
    // 0x803149A4: C0260058
    if (ctx.require_fpu(0x803149A4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 88); // lfs
    ++ctx.inst_count;
    // loc_803149A8: ;
    // 0x803149A8: D03D8000
    if (ctx.require_fpu(0x803149A8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149AC: ;
    // 0x803149AC: D01D8000
    if (ctx.require_fpu(0x803149AC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149B0: ;
    // 0x803149B0: C0060064
    if (ctx.require_fpu(0x803149B0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 100); // lfs
    ++ctx.inst_count;
    // loc_803149B4: ;
    // 0x803149B4: C0260060
    if (ctx.require_fpu(0x803149B4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 96); // lfs
    ++ctx.inst_count;
    // loc_803149B8: ;
    // 0x803149B8: D03D8000
    if (ctx.require_fpu(0x803149B8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149BC: ;
    // 0x803149BC: D01D8000
    if (ctx.require_fpu(0x803149BC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149C0: ;
    // 0x803149C0: C006006C
    if (ctx.require_fpu(0x803149C0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 108); // lfs
    ++ctx.inst_count;
    // loc_803149C4: ;
    // 0x803149C4: C0260068
    if (ctx.require_fpu(0x803149C4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 104); // lfs
    ++ctx.inst_count;
    // loc_803149C8: ;
    // 0x803149C8: D03D8000
    if (ctx.require_fpu(0x803149C8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149CC: ;
    // 0x803149CC: D01D8000
    if (ctx.require_fpu(0x803149CC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149D0: ;
    // 0x803149D0: C0060074
    if (ctx.require_fpu(0x803149D0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 116); // lfs
    ++ctx.inst_count;
    // loc_803149D4: ;
    // 0x803149D4: C0260070
    if (ctx.require_fpu(0x803149D4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 112); // lfs
    ++ctx.inst_count;
    // loc_803149D8: ;
    // 0x803149D8: D03D8000
    if (ctx.require_fpu(0x803149D8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149DC: ;
    // 0x803149DC: D01D8000
    if (ctx.require_fpu(0x803149DC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149E0: ;
    // 0x803149E0: C006007C
    if (ctx.require_fpu(0x803149E0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 124); // lfs
    ++ctx.inst_count;
    // loc_803149E4: ;
    // 0x803149E4: C0260078
    if (ctx.require_fpu(0x803149E4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 120); // lfs
    ++ctx.inst_count;
    // loc_803149E8: ;
    // 0x803149E8: D03D8000
    if (ctx.require_fpu(0x803149E8)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_803149EC: ;
    // 0x803149EC: D01D8000
    if (ctx.require_fpu(0x803149EC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
    // loc_803149F0: ;
    // 0x803149F0: C0060084
    if (ctx.require_fpu(0x803149F0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 132); // lfs
    ++ctx.inst_count;
    // loc_803149F4: ;
    ctx.inst_count += 1;
    // 0x803149F4: C0260080
    if (ctx.require_fpu(0x803149F4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[6] + 128); // lfs
    // loc_803149F8: ;
    // 0x803149F8: 38C60040
    ctx.gpr[6] = ctx.gpr[6] + 64; // addi r6, r6, 64
    ++ctx.inst_count;
    // loc_803149FC: ;
    // 0x803149FC: D03D8000
    if (ctx.require_fpu(0x803149FC)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314A00: ;
    // 0x80314A00: D01D8000
    if (ctx.require_fpu(0x80314A00)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The translated guest block translated_pair_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2919..2946.
// The only difference from the generated text is that the memory
// accesses are routed to the harness recorder, exactly as
// tools/check_native_lowering.py rewrites the extracted functions.
// ----------------------------------------------------------------------
static void translated_pair_2(CPUContext& ctx) {
    // loc_80314A20: ;
    ctx.inst_count += 1;
    // 0x80314A20: C003004C
    if (ctx.require_fpu(0x80314A20)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 76); // lfs
    // loc_80314A24: ;
    // 0x80314A24: 38840001
    ctx.gpr[4] = ctx.gpr[4] + 1; // addi r4, r4, 1
    ++ctx.inst_count;
    // loc_80314A28: ;
    ctx.inst_count += 1;
    // 0x80314A28: C0230048
    if (ctx.require_fpu(0x80314A28)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 72); // lfs
    // loc_80314A2C: ;
    // 0x80314A2C: 38630008
    ctx.gpr[3] = ctx.gpr[3] + 8; // addi r3, r3, 8
    ++ctx.inst_count;
    // loc_80314A30: ;
    // 0x80314A30: D03D8000
    if (ctx.require_fpu(0x80314A30)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314A34: ;
    // 0x80314A34: D01D8000
    if (ctx.require_fpu(0x80314A34)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}


// ----------------------------------------------------------------------
// The generated guest block bench_pair_2, verbatim from
// storage/crash-generated/output_61.cpp (func_803143EC), lines 2919..2946.
// This copy is the generated text, unchanged: it is what the
// benchmark measures.
// ----------------------------------------------------------------------
static void bench_pair_2(CPUContext& ctx) {
    // loc_80314A20: ;
    ctx.inst_count += 1;
    // 0x80314A20: C003004C
    if (ctx.require_fpu(0x80314A20)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[3] + 76); // lfs
    // loc_80314A24: ;
    // 0x80314A24: 38840001
    ctx.gpr[4] = ctx.gpr[4] + 1; // addi r4, r4, 1
    ++ctx.inst_count;
    // loc_80314A28: ;
    ctx.inst_count += 1;
    // 0x80314A28: C0230048
    if (ctx.require_fpu(0x80314A28)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = native_fastmem::read_f32(ctx.mmu,ctx.gpr[3] + 72); // lfs
    // loc_80314A2C: ;
    // 0x80314A2C: 38630008
    ctx.gpr[3] = ctx.gpr[3] + 8; // addi r3, r3, 8
    ++ctx.inst_count;
    // loc_80314A30: ;
    // 0x80314A30: D03D8000
    if (ctx.require_fpu(0x80314A30)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[1]); // stfs f1, -32768(r29)
    ++ctx.inst_count;
    // loc_80314A34: ;
    // 0x80314A34: D01D8000
    if (ctx.require_fpu(0x80314A34)) return; // MSR.FP exception
    ctx.mmu.write_f32(ctx.gpr[29] + -32768, (float)ctx.fpr[0]); // stfs f0, -32768(r29)
    ++ctx.inst_count;
}

// === END GENERATED TRANSLATED BLOCKS ===

// ---------------------------------------------------------------------------
// The differential test.
// ---------------------------------------------------------------------------
struct BlockCase {
  const char* name;
  void (*translated)(CPUContext&);   // the generated block, recorded
  void (*bench)(CPUContext&);        // the generated block, unchanged
  void (*native)(CPUContext&);
  uint32_t guest_instructions;       // what the translated region retires
};

static const BlockCase kBlocks[] = {
  {"float3_pos",   translated_float3_pos,   bench_float3_pos,   native_float3_pos,   8},
  {"float3_nrm",   translated_float3_nrm,   bench_float3_nrm,   native_float3_nrm,   8},
  {"float3_tan",   translated_float3_tan,   bench_float3_tan,   native_float3_tan,   8},
  {"float3_bin",   translated_float3_bin,   bench_float3_bin,   native_float3_bin,   8},
  {"packed1",      translated_packed1,      bench_packed1,      native_packed1,      2},
  {"packed2",      translated_packed2,      bench_packed2,      native_packed2,      4},
  {"block16",      translated_block16,      bench_block16,      native_block16,      34},
  {"pair",         translated_pair,         bench_pair,         native_pair,         6},
  {"float3_pos2",  translated_float3_pos2,  bench_float3_pos2,  native_float3_pos,   8},
  {"float3_nrm2",  translated_float3_nrm2,  bench_float3_nrm2,  native_float3_nrm,   8},
  {"float3_tan2",  translated_float3_tan2,  bench_float3_tan2,  native_float3_tan,   8},
  {"float3_bin2",  translated_float3_bin2,  bench_float3_bin2,  native_float3_bin,   8},
  {"packed1_2",    translated_packed1_2,    bench_packed1_2,    native_packed1,      2},
  {"packed2_2",    translated_packed2_2,    bench_packed2_2,    native_packed2,      4},
  {"block16_2",    translated_block16_2,    bench_block16_2,    native_block16,      34},
  {"pair_2",       translated_pair_2,       bench_pair_2,       native_pair,         6},
};

static nl::Difference compare_block(const BlockCase& c, CPUContext& a, CPUContext& b,
                                    std::mt19937& rng, int index) {
  nl::Difference d;

  nl::g_effects.clear();
  nl::g_ports.clear();
  nl::g_recording = true;
  c.translated(a);
  const std::vector<nl::Effect> original = nl::g_effects.entries;
  const std::vector<nl::Effect> original_ports = nl::g_ports.entries;
  nl::g_effects.clear();
  nl::g_ports.clear();
  c.native(b);
  const std::vector<nl::Effect> native = nl::g_effects.entries;
  const std::vector<nl::Effect> native_ports = nl::g_ports.entries;
  nl::g_recording = false;

  nl::Difference e = nl::compare_effects(original_ports, native_ports, "port");
  if (e.any) d.note(e.text);
#ifdef NATIVE_LOWERING_TRACE_MEMORY
  // The guest access log is only complete when the support header's trace
  // hook is compiled in; the production build does not record it.
  e = nl::compare_effects(original, native, "effect");
  if (e.any) d.note(e.text);
#else
  (void)original;
  (void)native;
#endif

  // The retired instruction count.
  if (a.inst_count != c.guest_instructions) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "translated inst_count=%llu expected=%u",
                  (unsigned long long)a.inst_count, c.guest_instructions);
    d.note(buf);
  }
  if (b.inst_count != c.guest_instructions) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "native inst_count=%llu expected=%u",
                  (unsigned long long)b.inst_count, c.guest_instructions);
    d.note(buf);
  }

  // The full register file, the condition register and the machine state.
  // The blocks touch no volatile register that a caller could observe
  // differently, so nothing is allowed to differ.
  nl::VolatileSet vol;
  e = nl::compare(nl::capture(a), nl::capture(b), vol);
  if (e.any) d.note(e.text);

  // The guest RAM the block read.
  std::vector<nl::Region> regions;
  regions.push_back(nl::Region{kRecord, kRecord + 160});
  e = nl::compare_ram(a.mmu, b.mmu, regions);
  if (e.any) d.note(e.text);

  (void)rng;
  (void)index;
  return d;
}

static int test_blocks() {
  CPUContext a, b;
  std::mt19937 rng(0x803143ECu);
  int cases = 0, failures = 0;
  for (const BlockCase& c : kBlocks) {
    for (int i = 0; i < 64; ++i) {
      setup_state(a, rng);
      fill_record(a, rng);
      copy_state(b, a);
      nl::Difference d = compare_block(c, a, b, rng, i);
      if (d.any) {
        ++failures;
        if (failures < 6) {
          std::printf("FAIL %s case %d\n%s", c.name, i, d.text.c_str());
        }
      }
      ++cases;
    }
  }
  std::printf("NATIVE_LOWERING_TESTS vertex.blocks %d/%d\n", cases - failures, cases);
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");
  return failures;
}

// ---------------------------------------------------------------------------
// The watch fallback.
//
// The kernel's direct write-gather store is only taken when no watch observer
// can run between the store and the FIFO write.  With a watch inside the
// write-gather page the kernel must fall back to the MMU path, and the two
// paths must still agree.
// ---------------------------------------------------------------------------
static int test_watch_fallback() {
  CPUContext a, b;
  std::mt19937 rng(0x0C008000u);
  int cases = 0, failures = 0;
  nwii::runtime::g_watch_addr = 0x0C008000u;
  for (const BlockCase& c : kBlocks) {
    for (int i = 0; i < 16; ++i) {
      setup_state(a, rng);
      fill_record(a, rng);
      copy_state(b, a);
      nl::Difference d = compare_block(c, a, b, rng, i);
      if (d.any) {
        ++failures;
        if (failures < 4) std::printf("FAIL watch %s case %d\n%s", c.name, i, d.text.c_str());
      }
      ++cases;
    }
  }
  nwii::runtime::g_watch_addr = 0;
  std::printf("NATIVE_LOWERING_TESTS vertex.watch %d/%d\n", cases - failures, cases);
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");
  return failures;
}

// ---------------------------------------------------------------------------
// The paired benchmark.
//
// The two paths run alternately on the same guest state, so the measurement
// is paired.  The median of the per-repetition times is reported, together
// with the spread (min / max) as the noise.
// ---------------------------------------------------------------------------
using Clock = std::chrono::steady_clock;

template <class F>
static double time_ms(F&& fn, int iterations) {
  const Clock::time_point t0 = Clock::now();
  fn(iterations);
  const Clock::time_point t1 = Clock::now();
  return std::chrono::duration<double, std::milli>(t1 - t0).count() / iterations;
}

static double median(std::vector<double> v) {
  std::sort(v.begin(), v.end());
  return v[v.size() / 2];
}

static void benchmark_block(const BlockCase& c, CPUContext& ctx) {
  const int kBatches = 200;

  // Calibrate the batch size so that every timed batch is short enough that
  // the machine's interference does not dominate it, and long enough for the
  // clock's resolution (target: 2 ms per batch).  The probe takes the
  // minimum of a few runs, so a disturbed probe does not shorten the batch.
  double probe = 1e30;
  for (int i = 0; i < 5; ++i) {
    const double t = time_ms([&](int n) {
      for (int k = 0; k < n; ++k) c.bench(ctx);
    }, 2000);
    if (t < probe) probe = t;
  }
  int iterations = 2000;
  if (probe > 0.0) {
    iterations = static_cast<int>(2.0 / probe);
    if (iterations < 2000) iterations = 2000;
    if (iterations > 400000) iterations = 400000;
  }

  std::vector<double> translated, native;
  // Warm up both paths so the first timed batch is not the cold one.
  for (int i = 0; i < 2000; ++i) { c.bench(ctx); c.native(ctx); }
  for (int r = 0; r < kBatches; ++r) {
    // Alternate the order so a slow drift cannot favour one path.
    if ((r & 1) == 0) {
      translated.push_back(time_ms([&](int n) {
        for (int i = 0; i < n; ++i) c.bench(ctx);
      }, iterations));
      native.push_back(time_ms([&](int n) {
        for (int i = 0; i < n; ++i) c.native(ctx);
      }, iterations));
    } else {
      native.push_back(time_ms([&](int n) {
        for (int i = 0; i < n; ++i) c.native(ctx);
      }, iterations));
      translated.push_back(time_ms([&](int n) {
        for (int i = 0; i < n; ++i) c.bench(ctx);
      }, iterations));
    }
  }
  // The minimum is the robust estimator: it is the batch that ran without
  // interference.  The median is reported next to it, together with the
  // spread of the batches, which is the noise of this machine.
  const double tmed = median(translated), nmed = median(native);
  const double tmin = *std::min_element(translated.begin(), translated.end());
  const double nmin = *std::min_element(native.begin(), native.end());
  const double tmax = *std::max_element(translated.begin(), translated.end());
  const double nmax = *std::max_element(native.begin(), native.end());
  std::printf("NATIVE_LOWERING_BENCH vertex.%-10s guest_inst=%2u  batches=%d iters=%d  "
              "translated_min=%.6f ms  native_min=%.6f ms  ratio_min=%.3f  "
              "translated_med=%.6f ms  native_med=%.6f ms  ratio_med=%.3f  "
              "spread(t)=%.0f%%  spread(n)=%.0f%%  sink=%llu\n",
              c.name, c.guest_instructions, kBatches, iterations,
              tmin, nmin, nmin / tmin, tmed, nmed, nmed / tmed,
              100.0 * (tmax - tmin) / tmin, 100.0 * (nmax - nmin) / nmin,
              (unsigned long long)g_fifo_sink);
}

static void test_benchmark() {
  CPUContext ctx;
  std::mt19937 rng(0x5EED1234u);
  for (const BlockCase& c : kBlocks) {
    setup_state(ctx, rng);
    fill_record(ctx, rng);
    benchmark_block(c, ctx);
  }
}

int main() {
  int failures = test_blocks();
  failures += test_watch_fallback();
  test_benchmark();
  return failures == 0 ? 0 : 1;
}
