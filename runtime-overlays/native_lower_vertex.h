#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Source-backed native lowerings for the *bounded blocks* of the descriptor
// driven GX vertex-attribute emitter, func_803143EC (and the identical
// sequence the function repeats later in its body).
//
// Why blocks and not the whole function: the whole function is already
// covered by the register-local kernel that
// native-port/tools/vertex_emit_kernel.py installs (that stage lifts the
// entire body into host registers).  What that kernel cannot do is shrink
// the *emission* code itself, and the emission code is what the capture
// attributes the time to: the generated body of func_803143EC contains 180
// `stfs` and 18 `stw` stores to the write-gather pipe (198 of its 530
// stores), and every one of them is surrounded by a per-instruction
// `require_fpu` test, a per-instruction `ctx.inst_count` update and a
// reload of the spilled guest registers.
//
// The blocks below are the generator-wide patterns of that code:
//
//   * a run of `lfs` loads from one vertex record followed by a run of `stfs`
//     stores to the write-gather pipe (the 3-float attribute group),
//   * the same for the 2-float attribute pair (the 16-store loop body and the
//     2-store loop body),
//   * a run of `lwz` loads followed by `stw` stores of the packed attribute
//     words (the colour / texcoord word pairs),
//   * the attribute-flag word load and its `rlwinm` bit extraction.
//
// Every block obeys the rules of native_lower_support.h:
//
//   * every guest access goes through `GuestMem`, so the watch, the MMIO
//     dispatch, the FIFO publication and the command order are the runtime's,
//   * guest addresses are 32-bit and wrap,
//   * no guest state is copied to the host: the register file the blocks
//     write is reached through `VertexRegs`, a set of pointers into the
//     guest's own `CPUContext`,
//   * the retired guest instruction count is accounted exactly: each kernel
//     retires precisely the guest instructions it replaces.
//
// Preconditions (the staged installation must check them; on any failure the
// untouched generated body runs):
//
//   * MSR.FP is enabled (`ctx.msr & 0x2000`).  Every block is a run of
//     load/store instructions that can raise the MSR.FP exception; with FP
//     enabled none of them can fault, so the block is atomic and the kernel
//     needs no per-instruction fault test.  (The generated body raises the
//     exception through `ctx.require_fpu(pc)` and returns; the staged site
//     keeps that path for the FP-disabled case.)
//   * the record the block reads is guest RAM.  The kernel does not require
//     this - `GuestMem::read_f32` takes the MMU path for every address - but
//     the *whole block* must not cross a page that the MMU classifies
//     differently, because the MMU's slow path may have side effects the
//     kernel's straight-line code would not reproduce.  The staged guard
//     therefore requires the record to be MEM1-resident.
//
// The guest instructions are quoted from the generated chunk
// (storage/crash-generated/output_61.cpp, func_803143EC).
#include "native_lower_support.h"

#include <array>
#include <cstdint>

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// The write-gather pipe.
//
// r29 holds 0xCC010000 in this function, so every `stfs f, -32768(r29)` and
// `stw r0, -32768(r29)` lands on 0xCC008000, the BP (XF) register write port.
// ---------------------------------------------------------------------------
inline constexpr uint32_t kVertexFifo = 0xCC008000u;

// The guest's floating-point register file and its PS1 companion array.  The
// blocks below write the same FPR/PS1 words the translated path writes, so
// the register file after the block is identical.
using VertexFpBank = std::array<double, 32>;

// The guest registers the vertex-emission blocks write.  The pointers refer
// to the guest's own `CPUContext` members, so the kernel writes the real
// state; nothing is copied to the host.
struct VertexRegs {
  uint32_t* gpr;                       // &ctx.gpr[0]
  nwii::runtime::ConditionField* cr;   // &ctx.cr[0]
  double* fpr;                         // &ctx.fpr[0]
  double* ps1;                         // &ctx.ps1[0]
};

inline VertexRegs vertex_regs(CPUContext& ctx) {
  VertexRegs r;
  r.gpr = ctx.gpr.data();
  r.cr = ctx.cr.data();
  r.fpr = ctx.fpr.data();
  r.ps1 = ctx.ps1.data();
  return r;
}

// ---------------------------------------------------------------------------
// The FIFO store.
//
// `MMU::write_f32` classifies the address on every store: it tests the
// watch snapshot, loads the page bias, and only then reaches the FIFO
// branch.  The kernel repeats the MMU's own FIFO condition and takes the
// port directly when the condition holds, which is exactly what
// `MMU::native_v13_checked_write_f32` would do:
//
//   * the address is on the 0x0C008000 page, and
//   * no watch observer can run between the store and the FIFO write
//     (`native_watch_span_may_hit` is the MMU's own conservative predicate,
//     and `custom_watch` is an empty inline function, so the MMU's slow path
//     has no other side effect).
//
// When the condition does not hold the kernel falls back to `GuestMem`,
// which reproduces the MMU path in full (including the watch hit).
// ---------------------------------------------------------------------------
inline bool vertex_fifo_watch_clear(uint32_t addr, uint32_t bytes) {
  const uint32_t paddr = addr & 0x3FFFFFFFu;
  if ((paddr & 0xFFFFF000u) != 0x0C008000u) return false;
  // No watch at all: the MMU's own predicate is false, so the FIFO branch is
  // taken.  This is the cheapest test and it covers the common case.
  if (nwii::runtime::g_watch_addr == 0) return true;
  if (!(native_launch_flags::wg_watch_possible ||
        nwii::runtime::g_watch_addr != native_launch_flags::watch)) {
    return true;
  }
  return !nwii::runtime::native_watch_span_may_hit(paddr, bytes);
}

// The write-gather store with the loop-invariant address test hoisted out.
// The watch test stays inside, because the MMU re-reads the watch on every
// store.
inline void vertex_wgp_f32(GuestMem mem, uint32_t addr, float value,
                           bool address_is_fifo) {
  if (__builtin_expect(address_is_fifo && vertex_fifo_watch_clear(addr, 4), 1)) {
    NWII_CTX_BARRIER();
    nwii::runtime::GX_WGPIPE_WriteF32(value);
    uint32_t bits;
    std::memcpy(&bits, &value, 4);
    NATIVE_LOWERING_TRACE(addr, 4, bits, true);
    return;
  }
  mem.write_f32(addr, value);
}

inline void vertex_wgp_f32(GuestMem mem, uint32_t addr, float value) {
  vertex_wgp_f32(mem, addr, value, addr == kVertexFifo);
}

// The direct write-gather store for a packed word.  It duplicates the MMU's
// FIFO classification, so it costs about thirty bytes of code, but it also
// avoids the MMU's `noinline` cold entry, which is what the generated code
// pays on every single store.
inline void vertex_wgp_u32(GuestMem mem, uint32_t addr, uint32_t value) {
  if (__builtin_expect(addr == kVertexFifo && vertex_fifo_watch_clear(addr, 4), 1)) {
    NWII_CTX_BARRIER();
    nwii::runtime::GX_WGPIPE_Write32(value);
    NATIVE_LOWERING_TRACE(addr, 4, value, true);
    return;
  }
  mem.write32(addr, value);
}

// ---------------------------------------------------------------------------
// vertex_emit_float3 - the 3-float vertex attribute group.
//
// Guest instructions (func_803143EC site 0x80314610, repeated at
// 0x80314634 for the normal and at 0x80314870 / 0x80314894 in the second
// copy of the sequence):
//
//   0x80314610  lfs   f2, off+8(r26)
//   0x80314614  lfs   f1, off+4(r26)
//   0x80314618  lfs   f0, off+0(r26)
//   0x8031461C  stfs  f0, -32768(r29)
//   0x80314620  stfs  f1, -32768(r29)
//   0x80314624  stfs  f2, -32768(r29)
//
// Contract:
//   inputs   record  the vertex record base (r26)
//            off     the byte offset of the attribute inside the record
//            fifo    the write-gather address (r29 - 32768)
//   memory   3 x read f32 at record+off+{0,4,8}, in that order
//            3 x write f32 at fifo, in the order f0, f1, f2
//   registers
//            fpr[0..2] and ps1[0..2] receive the three loaded values
//            (f2 first, then f1, then f0 - the guest loads in reverse)
//            no other register is touched
//   cr       untouched (lfs and stfs do not set the condition register)
//   inst_count
//            +6, the six guest instructions the block replaces
//   polls    none
// ---------------------------------------------------------------------------
inline void vertex_emit_float3(GuestMem mem, uint64_t& inst_count,
                                VertexRegs regs,
                                uint32_t record, uint32_t off, uint32_t fifo) {
  const bool direct = (fifo == kVertexFifo);
  const float v2 = mem.read_f32(record + off + 8u);
  const float v1 = mem.read_f32(record + off + 4u);
  const float v0 = mem.read_f32(record + off);
  regs.fpr[2] = regs.ps1[2] = static_cast<double>(v2);
  regs.fpr[1] = regs.ps1[1] = static_cast<double>(v1);
  regs.fpr[0] = regs.ps1[0] = static_cast<double>(v0);
  vertex_wgp_f32(mem, fifo, v0, direct);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v2, direct);
  inst_count += 6;
}

// ---------------------------------------------------------------------------
// vertex_emit_flag_bit - the attribute-flag word and its bit extraction.
//
// Guest instructions (func_803143EC site 0x80314628, and the same pair at
// 0x8031464C, 0x80314670, 0x80314694, 0x8031488C, 0x803148D0, 0x803148EC,
// 0x803148F4 and 0x8031490C):
//
//   0x80314628  lwz    r0, 4(r26)
//   0x8031462C  rlwinm r0, r0, 0, 27, 27
//
// The `rlwinm` is `rotl(value, 0) & mask`.  The mask follows the PowerPC
// convention, where the mask bit index counts from the most significant bit,
// so `rlwinm ..., 27, 27` selects the mask 0x10 - the value the generated
// body uses.  The `mask` argument below is that mask, not the bit index.
//
// The `lwz` destination is not always r0: the fourth attribute group loads
// the word into r3 and extracts the colour bit from it
// (`lwz r3, 4(r26)` / `rlwinm r0, r3, 0, 26, 26`, mask 0x20), so the source
// register is a parameter.
//
// Contract:
//   inputs   record       the vertex record base (r26)
//            mask         the `rlwinm` mask (0x10 for the normal, 0x800 for
//                           the tangent, 0x1000 for the binormal, 0x20 for
//                           the colour)
//            source_reg   the register the `lwz` writes (r0 or r3)
//   memory   1 x read u32 at record+4
//   registers
//            gpr[source_reg] receives the word
//            gpr[0] receives the extracted bit
//            cr[0].lt / cr[0].gt / cr[0].eq are recomputed from it, exactly
//            as the generated body does (cr[0].so is untouched)
//   inst_count
//            +2
//   polls    none
// ---------------------------------------------------------------------------
inline void vertex_emit_flag_bit(GuestMem mem, uint64_t& inst_count,
                                 VertexRegs regs,
                                 uint32_t record, uint32_t mask, uint32_t source_reg) {
  const uint32_t word = mem.read32(record + 4u);
  regs.gpr[source_reg] = word;
  const uint32_t value = std::rotl(regs.gpr[source_reg], 0) & mask;
  regs.gpr[0] = value;
  regs.cr[0].lt = (static_cast<int32_t>(value) < 0);
  regs.cr[0].gt = (static_cast<int32_t>(value) > 0);
  regs.cr[0].eq = (value == 0);
  inst_count += 2;
}

// ---------------------------------------------------------------------------
// vertex_emit_block16 - the 16-store attribute loop body.
//
// Guest instructions (func_803143EC site 0x8031471C, repeated at
// 0x8031497C).  The body reads the 16 attribute floats of the record
// (offsets 72..132, eight consecutive pairs) and writes them to the
// write-gather pipe:
//
//   0x8031471C  lfs  f1, 76(r6)      0x8031471C+0x00
//   0x80314720  addi r4, r4, 8
//   0x80314724  lfs  f0, 72(r6)
//   0x80314728  stfs f0, -32768(r29)
//   0x8031472C  stfs f1, -32768(r29)
//   ... eight (lfs, lfs, stfs, stfs) groups ...
//   0x80314790  lfs  f0, 132(r6)
//   0x80314794  lfs  f1, 128(r6)
//   0x80314798  addi r6, r6, 64
//   0x8031479C  stfs f1, -32768(r29)
//   0x803147A0  stfs f0, -32768(r29)
//
// The trip count of the enclosing loop is one: the loop is only entered
// when the attribute count is 9..15, and its counter is (count-1)>>3.
//
// Contract:
//   inputs   base    the vertex record base (r6)
//            fifo    the write-gather address (r29 - 32768)
//   memory   16 x read f32 at base+{72,76,80,...,132}, in the guest's order
//            (the high element of a pair is read first)
//            16 x write f32 at fifo, in ascending attribute order
//   registers
//            fpr[0], fpr[1] and ps1[0], ps1[1] hold the last pair
//            gpr[4] is incremented by 8 and gpr[6] by 64
//   cr       untouched
//   inst_count
//            +34, the 34 guest instructions the block replaces
//   polls    none (the loop's poll point is the branch at 0x803147A4, which
//            stays in the generated body)
// ---------------------------------------------------------------------------
inline void vertex_emit_block16(GuestMem mem, uint64_t& inst_count,
                                 VertexRegs regs,
                                 uint32_t base, uint32_t fifo) {
  const bool direct = (fifo == kVertexFifo);
  float v0, v1;
  v1 = mem.read_f32(base + 76u);
  regs.gpr[4] = regs.gpr[4] + 8u;
  v0 = mem.read_f32(base + 72u);
  vertex_wgp_f32(mem, fifo, v0, direct);
  vertex_wgp_f32(mem, fifo, v1, direct);
  v0 = mem.read_f32(base + 84u);
  v1 = mem.read_f32(base + 80u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 92u);
  v1 = mem.read_f32(base + 88u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 100u);
  v1 = mem.read_f32(base + 96u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 108u);
  v1 = mem.read_f32(base + 104u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 116u);
  v1 = mem.read_f32(base + 112u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 124u);
  v1 = mem.read_f32(base + 120u);
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  v0 = mem.read_f32(base + 132u);
  v1 = mem.read_f32(base + 128u);
  regs.gpr[6] = regs.gpr[6] + 64u;
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  regs.fpr[0] = regs.ps1[0] = static_cast<double>(v0);
  regs.fpr[1] = regs.ps1[1] = static_cast<double>(v1);
  inst_count += 34;
}

// ---------------------------------------------------------------------------
// vertex_emit_pair - the 2-store attribute loop body.
//
// Guest instructions (func_803143EC site 0x803147C0, repeated at
// 0x80314A44):
//
//   0x803147C0  lfs   f0, 76(r3)
//   0x803147C4  addi  r4, r4, 1
//   0x803147C8  lfs   f1, 72(r3)
//   0x803147CC  addi  r3, r3, 8
//   0x803147D0  stfs  f1, -32768(r29)
//   0x803147D4  stfs  f0, -32768(r29)
//
// The loop is the "extra attributes" loop; its trip count is the attribute
// count (flags & 0xF), at most 15.
//
// Contract:
//   inputs   fifo    the write-gather address (r29 - 32768)
//   memory   1 x read f32 at r3+76, 1 x read f32 at r3+72
//            2 x write f32 at fifo (f1 then f0)
//   registers
//            fpr[0], fpr[1] and ps1[0], ps1[1] receive the two values
//            gpr[3] is incremented by 8 and gpr[4] by 1
//   cr       untouched
//   inst_count
//            +6
//   polls    none (the loop's poll point is the branch at 0x803147D8, which
//            stays in the generated body)
// ---------------------------------------------------------------------------
inline void vertex_emit_pair(GuestMem mem, uint64_t& inst_count,
                             VertexRegs regs,
                             uint32_t fifo) {
  const bool direct = (fifo == kVertexFifo);
  const float v0 = mem.read_f32(regs.gpr[3] + 76u);
  regs.gpr[4] = regs.gpr[4] + 1u;
  const float v1 = mem.read_f32(regs.gpr[3] + 72u);
  regs.gpr[3] = regs.gpr[3] + 8u;
  vertex_wgp_f32(mem, fifo, v1, direct);
  vertex_wgp_f32(mem, fifo, v0, direct);
  regs.fpr[0] = regs.ps1[0] = static_cast<double>(v0);
  regs.fpr[1] = regs.ps1[1] = static_cast<double>(v1);
  inst_count += 6;
}

// ---------------------------------------------------------------------------
// vertex_emit_packed1 - one packed attribute word.
//
// Guest instructions (func_803143EC site 0x803146A0, repeated at
// 0x80314900):
//
//   0x803146A0  lwz  r0, 28(r26)
//   0x803146A4  stw  r0, -32768(r29)
//
// Contract:
//   inputs   record  the vertex record base (r26)
//            fifo    the write-gather address
//   memory   1 x read u32 at record+28, 1 x write u32 at fifo
//   registers
//            gpr[0] receives the word
//   cr       untouched
//   inst_count
//            +2
//   polls    none
// ---------------------------------------------------------------------------
inline void vertex_emit_packed1(GuestMem mem, uint64_t& inst_count,
                                 VertexRegs regs,
                                 uint32_t record, uint32_t fifo) {
  const uint32_t word = mem.read32(record + 28u);
  regs.gpr[0] = word;
  vertex_wgp_u32(mem, fifo, word);
  inst_count += 2;
}

// ---------------------------------------------------------------------------
// vertex_emit_packed2 - the pair of packed attribute words.
//
// Guest instructions (func_803143EC site 0x803146B4, repeated at
// 0x80314914):
//
//   0x803146B4  lwz  r0, 28(r26)
//   0x803146B8  stw  r0, -32768(r29)
//   0x803146BC  lwz  r0, 32(r26)
//   0x803146C0  stw  r0, -32768(r29)
//
// Contract:
//   inputs   record  the vertex record base (r26)
//            fifo    the write-gather address
//   memory   1 x read u32 at record+28, 1 x write u32 at fifo,
//            1 x read u32 at record+32, 1 x write u32 at fifo
//   registers
//            gpr[0] receives the second word
//   cr       untouched
//   inst_count
//            +4
//   polls    none
// ---------------------------------------------------------------------------
inline void vertex_emit_packed2(GuestMem mem, uint64_t& inst_count,
                                 VertexRegs regs,
                                 uint32_t record, uint32_t fifo) {
  uint32_t word = mem.read32(record + 28u);
  regs.gpr[0] = word;
  vertex_wgp_u32(mem, fifo, word);
  word = mem.read32(record + 32u);
  regs.gpr[0] = word;
  vertex_wgp_u32(mem, fifo, word);
  inst_count += 4;
}

// ---------------------------------------------------------------------------
// How a staged installation uses these kernels.
//
// The generated body of func_803143EC owns the blocks; a stage that wants the
// native path replaces one block region with the kernel call.  The staged
// site looks like this (the region is the generated code between the
// `loc_` label of the block's first instruction and the last instruction of
// the block, with the block's own increments removed):
//
//     if (ctx.msr & 0x2000u && <record is MEM1-resident>) {
//         native_lower::vertex_emit_block16(native_lower::guest_mem(ctx),
//                                             ctx.inst_count,
//                                             native_lower::vertex_regs(ctx),
//                                             ctx.gpr[6],
//                                             ctx.gpr[29] - 32768u);
//     } else {
//         <the untouched generated block>
//     }
//
// The guard is deliberately the same shape as the one the whole-function
// kernel uses (tools/vertex_emit_kernel.py), except that it does *not*
// require "no watch at all": the kernels keep the MMU's own watch predicate,
// so they are usable with the production watch (0x80597790, the scheduler
// counter) in place.  The whole-function kernel's guard
//
//     if (nwii::runtime::g_watch_addr != 0 || ...) return false;
//
// rejects every production configuration, because the runtime's default
// `g_watch_addr` is 0x80597790 (see src/core/os_hle_stubs.cpp), not 0.
//
// The fallback is the untouched generated block: the stage keeps the
// original text and only replaces it when the guard holds, so an
// unsupported case (FP disabled, a record outside MEM1, a traced call) runs
// the original code with its original MSR.FP exception behaviour.
//
// The poll points are not inside any of these blocks: the two loops poll at
// their branch instruction (0x803147A4 and 0x803147D8 in the first copy,
// 0x80314A08 and 0x80314A38 in the second), which stay in the generated
// body, so the guest's scheduling is unchanged.
//
// ---------------------------------------------------------------------------
// The installable sites.
//
// The generated body accounts the retired count in chunks: a chunk that can
// raise the MSR.FP exception is counted *before* the instruction (and undone
// when the exception is taken), every other chunk is counted *after* it.  A
// block can therefore replace a region of the generated body without changing
// the guest's instruction count only when the region's increments account for
// exactly the instructions inside it.  The regions below are the ones that
// satisfy that; each starts at a `loc_` label (so the increment that belongs
// to the previous instruction stays outside) and ends at the last
// instruction of the block.
//
//   guest instructions              count   kernel
//   0x80314610 .. 0x8031462C          8      float3 (6) + flag_bit (2)
//   0x80314634 .. 0x80314650          8      float3 (6) + flag_bit (2)
//   0x80314658 .. 0x80314674          8      float3 (6) + flag_bit (2)
//   0x8031467C .. 0x80314698          8      float3 (6) + flag_bit (2)
//   0x803146A0 .. 0x803146A4          2      packed1
//   0x803146B4 .. 0x803146C0          4      packed2
//   0x8031471C .. 0x803147A0         34      block16
//   0x803147C0 .. 0x803147D4          6      pair
//   0x80314870 .. 0x8031488C          8      float3 (6) + flag_bit (2)
//   0x80314894 .. 0x803148B0          8      float3 (6) + flag_bit (2)
//   0x803148B8 .. 0x803148D4          8      float3 (6) + flag_bit (2)
//   0x803148DC .. 0x803148F8          8      float3 (6) + flag_bit (2)
//   0x80314900 .. 0x80314904          2      packed1
//   0x80314914 .. 0x80314920          4      packed2
//   0x8031497C .. 0x80314A00         34      block16
//   0x80314A44 .. 0x80314A58          6      pair
//
// The attribute offsets and the flag masks of the four float3 groups are
// (position, normal, tangent, binormal):
//
//   0x80314610 / 0x80314870   off 16   mask 0x10   (the normal is present)
//   0x80314634 / 0x80314894   off 36   mask 0x800  (the tangent is present)
//   0x80314658 / 0x803148B8   off 48   mask 0x1000 (the binormal is present)
//   0x8031467C / 0x803148DC   off 60   mask 0x1000 (the binormal is present)
//
// The last group is followed by the colour-word test (mask 0x20) and the
// second packed-word test (mask 0x4000), which are part of the packed-word
// blocks.
//
// Measured on the production build (-O2 -fwrapv -fno-strict-aliasing
// -ffp-contract=off, x86-64), one block against the generated code:
//
//   block        generated            native kernel         delta
//   float3       298 instr / 1338 B   280 instr / 1204 B   -18 / -134
//   packed1       49 instr /  204 B    74 instr /  296 B   +25 /  +92
//   packed2       93 instr /  406 B   139 instr /  549 B   +46 / +143
//   block16     1292 instr / 6037 B  1073 instr / 4646 B  -219 / -1391
//   pair         188 instr /  842 B   161 instr /  669 B   -27 / -173
//
// (the `noinline` wrappers the differential test uses; the numbers exclude
// the `.cold` part, which the generated code keeps at 9-49 instructions and
// the kernel at 10-88.)
//
// The packed-word blocks are the only ones that grow: their duplicated FIFO
// classification is not amortised over enough stores.  The other four shrink.
// ---------------------------------------------------------------------------
inline constexpr uint32_t kVertexFloat3Count = 6;
inline constexpr uint32_t kVertexFlagBitCount = 2;
inline constexpr uint32_t kVertexPacked1Count = 2;
inline constexpr uint32_t kVertexPacked2Count = 4;
inline constexpr uint32_t kVertexBlock16Count = 34;
inline constexpr uint32_t kVertexPairCount = 6;

}  // namespace nwii::runtime::native_lower
