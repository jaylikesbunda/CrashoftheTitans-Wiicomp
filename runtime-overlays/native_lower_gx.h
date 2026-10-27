#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Source-backed native lowering of the Revolution SDK "GX" entry points that
// the native port currently executes as translated guest code.
//
// Every function in this header is written from the recovered C sources in
//   src/external/ogws/revolution/GX/*.c   (the "gxdt" flavour)
//   src/external/petari/RVL_SDK/gx/*.c     (the "gx" flavour)
// and is checked against the translated guest body, which is the ground truth
// for the observable behaviour.  The translated bodies live in
//   .../crash-generated/output_76.cpp
// and are quoted below by address so every derived constant can be checked.
//
// Rules honoured here (see the shared brief):
//   * every guest memory effect goes through `GuestMem` (the runtime MMU), so
//     watchpoints, MMIO and FIFO ordering are unchanged;
//   * guest addresses are 32 bit and wrap - all arithmetic is `uint32_t`;
//   * no host copy of a guest global: the GX state block is read and written
//     in guest memory through the pointer the guest keeps in its SDA;
//   * the guest's own layout is used, never a host `sizeof`/`offsetof`;
//   * single precision arithmetic with `-ffp-contract=off` semantics; the
//     recovered ogws source compares against *double* literals (`1.0l`,
//     `0.5l`), and so does the guest.
//
// ---------------------------------------------------------------------------
// The guest GX state block
// ---------------------------------------------------------------------------
//
// The guest keeps the pointer to the GX state block in a small-data-area word:
//
//   *(u32 *)(r2 - 0x38E0)            -> GX state block pointer
//
// Evidence: every GX entry reads it with `lwz rX, -0x38E0(r2)`
//   (translated: `native_fastmem::read32(ctx.mmu, ctx.gpr[2] + -14560)`)
// in, for example, func_803C9720 (GXClearVtxDesc), func_803C9754
// (GXSetVtxAttrFmt), func_803C9B88 (GXSetTexCoordGen2), func_803C9B38
// (GXSetArray), func_803CB7E0 (GXSetChanCtrl), func_803CD2A0 (GXSetFog),
// func_803CD4CC (GXSetFogRangeAdj), func_803CD698 (GXSetZMode),
// func_803CB590 (GXLoadLightObjImm).
//
// The state block offsets used by the lowerings below were derived from the
// store/load displacements of the translated guest code:
//
//   +0x000 (0)     u32   (unused by the lowerings below)
//   +0x002 (2)     u16   lastWriteWasXF / bpSentNot flag
//                        - 0x803CD6C4 `sth r0, 2(r9)` after a BP register load
//                          (GXSetZMode sets it to 0)
//                        - 0x803CB604 `sth r0, 2(r3)` with r0 = 1
//                          (GXLoadLightObjImm sets it to 1)
//   +0x014 (20)    u32   vcdLoReg   (GXClearVtxDesc: `stw r3, 20(r5)`)
//   +0x018 (24)    u32   vcdHiReg   (GXClearVtxDesc: `stw r4, 24(r5)`)
//   +0x01C (28)    u32[8] vatA      (GXSetVtxAttrFmt: `lwz r0, 28(r8)`)
//   +0x03C (60)    u32[8] vatB      (GXSetVtxAttrFmt: `lwz r0, 60(r8)`)
//   +0x05C (92)    u32[8] vatC      (GXSetVtxAttrFmt: `lwz r0, 92(r8)`)
//   +0x0B8 (184)   u32[4] chanCtrl  (GXSetChanCtrl: `stw r10, 184(r4)`)
//   +0x0228 (552)  u32   zMode      (GXSetZMode: `lwz/stw rX, 552(r9)`)
//   +0x524 (1316)  u8    normal     (GXClearVtxDesc: `stb r4, 1316(r5)`)
//   +0x525 (1317)  u8    binormal   (GXClearVtxDesc: `stb r4, 1317(r5)`)
//   +0x5FB (1531)  u8    vatDirtyFlags (GXSetVtxAttrFmt: `lbz/stb rX, 1531(r5)`)
//   +0x5FC (1532)  u32   dirty flags (GXClearVtxDesc: `ori r0, r0, 8`,
//                        GXSetVtxAttrFmt: `ori r3, r4, 16`,
//                        GXSetChanCtrl: `ori r0, r0, 0x5000` / `0xA000`)
//
// The vatA/vatB/vatC arrays are indexed by `fmt`: the translated code builds
// `r8 = gxdt + (fmt << 2)` and then accesses `28(r8)`, `60(r8)` and `92(r8)`,
// i.e. `gxdt + 28 + fmt*4`, `gxdt + 60 + fmt*4` and `gxdt + 92 + fmt*4`.
//
// The hardware ports used by the GX family:
//
//   0xCC008000   BP/XF register write port.  The guest reaches it as
//                `r4 = 0xCC010000; stb/stw rX, -0x8000(r4)`, i.e. the port
//                address is `0xCC010000 - 0x8000`.  The runtime maps the whole
//                0x0C008xxx page onto the write-gather pipe.
//   0xCC000000   CP command FIFO write port (not used by the lowerings
//                below; the guest reaches it as `0xCC010000 - 0x10000`).
//
// The "BP register load" protocol the guest uses is a one byte tag (0x61)
// followed by the 32 bit register value, both written to 0xCC008000; the
// runtime turns them into the FIFO byte stream.  The CP register load
// protocol used by GXSetArray is the tag 0x08, a register index byte and the
// 32 bit value.
//
// ---------------------------------------------------------------------------
// Small data area constants used by GXSetFog
// ---------------------------------------------------------------------------
//
// GXSetFog reads its FP constants from the SDA.  The offsets below are the
// displacements of the translated `lfs`/`lfd` instructions; the *values* are
// read from guest memory by the lowering, so the guest's own constants are
// used and not host literals.  For the record (main.dol, SDA = 0x8059FDC0):
//
//   sda - 0x3870 (-14448)  f32  0.0f
//   sda - 0x3874 (-14444)  f32  1.0f
//   sda - 0x3878 (-14440)  f32  0.5f
//   sda - 0x3888 (-14432)  f64  1.0
//   sda - 0x3898 (-14424)  f32  2.0f
//   sda - 0x38A8 (-14416)  f64  0.5
//   sda - 0x38B0 (-14408)  f32  8388638.0f
//   sda - 0x38C0 (-14400)  f64  4503601774854144.0 (= 1.375 * 2^52)
//
// GXSetFog also calls the SDK float helper at 0x80393518, which turns the
// float `magB` into the fixed point mantissa the BP register wants.  The
// helper reads three doubles from the absolute address 0x80484950
// (0.0, 4294967296.0 and 2147483648.0) and is reproduced below; the lowering
// reads them from guest memory as well.

#include "runtime/cpu_context.h"
#include "native_lower_support.h"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// Derived guest layout
// ---------------------------------------------------------------------------
namespace gx {

// The SDA word that holds the pointer to the GX state block.
inline constexpr int32_t kStatePtrOffset = -14560;  // -0x38E0

// GX state block field offsets (see the table above).
inline constexpr uint32_t kLastWriteWasXF = 2;
inline constexpr uint32_t kVcdLoReg = 20;
inline constexpr uint32_t kVcdHiReg = 24;
inline constexpr uint32_t kVatA = 28;
inline constexpr uint32_t kVatB = 60;
inline constexpr uint32_t kVatC = 92;
inline constexpr uint32_t kChanCtrl = 184;
inline constexpr uint32_t kZMode = 552;
inline constexpr uint32_t kNormal = 1316;
inline constexpr uint32_t kBinormal = 1317;
inline constexpr uint32_t kVatDirtyFlags = 1531;
inline constexpr uint32_t kDirtyFlags = 1532;
// GXSetTexCoordGen2's state block fields (derived from the store displacements
// of the translated body; see the function's contract below).
inline constexpr uint32_t kMatrixIndex0 = 128;
inline constexpr uint32_t kMatrixIndex1 = 132;
inline constexpr uint32_t kTexRegs = 200;       // u32[8]
inline constexpr uint32_t kDualTexRegs = 232;   // u32[8]
// GXSetTexCoordGen2's two switch tables in the guest's data section: the
// `src` table is indexed by `src` (21 entries, the guest bounds it above with
// `cmpwi r5, 20`), the `dst_coord` table by `dst_coord` (7 entries, the guest
// bounds it above with `cmpwi r3, 6`).  Both are read from main.dol:
//   0x8057661C: BB4 BC0 BCC BD8 BF4 BFC C04 C0C C14 C1C C24 C2C C30 C30 C30
//               C30 C30 C30 C30 BE4 BEC
//   0x80576600: D24 D34 D44 D54 D64 D74 D84
inline constexpr uint32_t kTexCoordSrcJumpTable = 0x8057661Cu;
inline constexpr uint32_t kTexCoordIdJumpTable = 0x80576600u;

// The BP/XF register write port (the guest computes 0xCC010000 - 0x8000).
inline constexpr uint32_t kBpPort = 0xCC008000u;
// The tag the guest writes before a BP register value.
inline constexpr uint8_t kBpLoadTag = 0x61u;
// The tag the guest writes before a CP register index/value pair.
inline constexpr uint8_t kCpLoadTag = 0x08u;
// The tag the guest writes before a CP "load XF registers" command.
inline constexpr uint8_t kCpXfLoadTag = 0x10u;

// The SDK float helper's constant table (func_80393518 reads it).
inline constexpr uint32_t kFloatToFixedTable = 0x80484950u;

// The SDA constants GXSetFog uses.
inline constexpr int32_t kFogZeroF32 = -14448;   // 0.0f
inline constexpr int32_t kFogOneF32 = -14444;    // 1.0f
inline constexpr int32_t kFogHalfF32 = -14440;   // 0.5f
inline constexpr int32_t kFogOneF64 = -14432;    // 1.0
inline constexpr int32_t kFogTwoF32 = -14424;    // 2.0f
inline constexpr int32_t kFogHalfF64 = -14416;   // 0.5
inline constexpr int32_t kFogMagF32 = -14408;    // 8388638.0f
inline constexpr int32_t kFogDivF64 = -14400;    // 1.375 * 2^52

// The GX attribute numbers the guest uses.  They are derived from the guest's
// own switch tables (main.dol, jump table at 0x80576578) and from
// GXSetArray's `cmpwi r3, 25` / `li r3, 10`:
//   GX_VA_POS = 9, GX_VA_NRM = 10, GX_VA_CLR0 = 11, GX_VA_CLR1 = 12,
//   GX_VA_TEX0 = 13 .. GX_VA_TEX7 = 20, GX_VA_NBT = 25.
inline constexpr uint32_t kAttrPos = 9;
inline constexpr uint32_t kAttrNrm = 10;
inline constexpr uint32_t kAttrClr0 = 11;
inline constexpr uint32_t kAttrClr1 = 12;
inline constexpr uint32_t kAttrTex0 = 13;
inline constexpr uint32_t kAttrTex7 = 20;
inline constexpr uint32_t kAttrNbt = 25;

// GXSetVtxAttrFmt's switch table lives in the guest's data section (the
// guest computes it with `lis r4, 0x8057; addi r4, r4, 0x6578`).
inline constexpr uint32_t kVtxAttrFmtJumpTable = 0x80576578u;

// The interior entries of func_803C9754 the jump table can select.
inline constexpr uint32_t kCasePos = 0x803C9784u;
inline constexpr uint32_t kCaseNrm = 0x803C979Cu;
inline constexpr uint32_t kCaseClr0 = 0x803C97D8u;
inline constexpr uint32_t kCaseClr1 = 0x803C97ECu;
inline constexpr uint32_t kCaseTex0 = 0x803C9800u;
inline constexpr uint32_t kCaseTex1 = 0x803C9818u;
inline constexpr uint32_t kCaseTex2 = 0x803C9830u;
inline constexpr uint32_t kCaseTex3 = 0x803C9848u;
inline constexpr uint32_t kCaseTex4 = 0x803C9860u;
inline constexpr uint32_t kCaseTex5 = 0x803C9880u;
inline constexpr uint32_t kCaseTex6 = 0x803C9898u;
inline constexpr uint32_t kCaseTex7 = 0x803C98B0u;
inline constexpr uint32_t kCaseDefault = 0x803C98C4u;

// The interior entries of func_803C9B88 the two jump tables can select.  The
// `src` table targets the first block, the `dst_coord` table targets the
// second block (0x803C9D94 is the `dst_coord > 6` default, not a table entry).
inline constexpr uint32_t kTexCaseSrcPos = 0x803C9BB4u;      // inputRow 0, ABC1
inline constexpr uint32_t kTexCaseSrcNrm = 0x803C9BC0u;      // inputRow 1, ABC1
inline constexpr uint32_t kTexCaseSrcBinrm = 0x803C9BCCu;    // inputRow 3, ABC1
inline constexpr uint32_t kTexCaseSrcTangent = 0x803C9BD8u;  // inputRow 4, ABC1
inline constexpr uint32_t kTexCaseSrcColor = 0x803C9BE4u;    // inputRow 2
inline constexpr uint32_t kTexCaseSrcColor1 = 0x803C9BECu;   // inputRow 2
inline constexpr uint32_t kTexCaseSrcRow5 = 0x803C9BF4u;     // inputRow 5
inline constexpr uint32_t kTexCaseSrcRow6 = 0x803C9BFCu;
inline constexpr uint32_t kTexCaseSrcRow7 = 0x803C9C04u;
inline constexpr uint32_t kTexCaseSrcRow8 = 0x803C9C0Cu;
inline constexpr uint32_t kTexCaseSrcRow9 = 0x803C9C14u;
inline constexpr uint32_t kTexCaseSrcRow10 = 0x803C9C1Cu;
inline constexpr uint32_t kTexCaseSrcRow11 = 0x803C9C24u;
inline constexpr uint32_t kTexCaseSrcRow12 = 0x803C9C2Cu;
inline constexpr uint32_t kTexCaseSrcDefault = 0x803C9C30u;  // inputRow 5, AB11
inline constexpr uint32_t kTexCaseId0 = 0x803C9D24u;
inline constexpr uint32_t kTexCaseId1 = 0x803C9D34u;
inline constexpr uint32_t kTexCaseId2 = 0x803C9D44u;
inline constexpr uint32_t kTexCaseId3 = 0x803C9D54u;
inline constexpr uint32_t kTexCaseId4 = 0x803C9D64u;
inline constexpr uint32_t kTexCaseId5 = 0x803C9D74u;
inline constexpr uint32_t kTexCaseId6 = 0x803C9D84u;
inline constexpr uint32_t kTexCaseIdDefault = 0x803C9D94u;   // dst_coord > 6

}  // namespace gx

// ---------------------------------------------------------------------------
// The guest poll
// ---------------------------------------------------------------------------
//
// Fog calculations before the first FIFO write have no guest-state writes.
// If a scheduler poll becomes due, abandon those local calculations and rerun
// the translated body from entry. Restore the entry poll credit so it reaches
// the same poll with its real register file, stack frame and instruction count.
// In particular, never call process_pending_callbacks with unpublished locals:
// it may save a context and longjmp without returning to this helper.
struct GuestPoll {
  CPUContext& ctx;
  const uint32_t entry_pc;
  const int32_t entry_credit;

  explicit GuestPoll(CPUContext& c)
      : ctx(c), entry_pc(c.pc), entry_credit(nwii::runtime::g_native_poll_credit) {}

  bool poll(uint32_t) {
    if (!nwii::runtime::native_poll_due()) return false;
    nwii::runtime::g_native_poll_credit = entry_credit;
    ctx.pc = entry_pc;
    return true;
  }
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// The guest's `fcmpu`: the comparison is unordered for a NaN, so every
// relational test is false and the "summary overflow" bit is set.
struct GxCmp {
  bool lt, gt, eq, so;
};

inline GxCmp gx_fcmpu(double a, double b) {
  const bool un = std::isnan(a) || std::isnan(b);
  GxCmp r;
  r.lt = !un && (a < b);
  r.gt = !un && (a > b);
  r.eq = !un && (a == b);
  r.so = un;
  return r;
}

// The bit pattern of a float, as the guest's `stfs`/`lwz` pairs see it.
inline uint32_t f32_bits(float v) {
  uint32_t b;
  std::memcpy(&b, &v, 4);
  return b;
}

inline float bits_f32(uint32_t b) {
  float v;
  std::memcpy(&v, &b, 4);
  return v;
}

// The guest's `fctiwz`: the truncation of a double to a 32 bit integer.  A
// host cast is undefined outside the range, so the guest's behaviour (the
// "indefinite" value 0x80000000) is spelled out.
inline uint32_t gx_fctiwz(double v) {
  if (std::isnan(v)) return 0x80000000u;
  if (!(v >= -2147483648.0 && v < 2147483648.0)) return 0x80000000u;
  return static_cast<uint32_t>(static_cast<int32_t>(v));
}

// The SDK float helper at 0x80393518: it turns a float into the fixed point
// mantissa a BP register field expects.
//
//   translated: func_80393518
//     lfd  f0, 0(r4)      ; c0
//     lfd  f3, 8(r4)      ; c1
//     lfd  f4, 0x10(r4)   ; c2
//     fcmpu cr0, f1, f0 ; blt -> return 0
//     li   r3, -1       ; fcmpu cr6, f1, f3 ; bge -> return -1
//     f2 = (f1 < c2) ? f1 : f1 - c2 ; fctiwz f2 ; r3 = the integer
//     if (f1 >= c2) r3 += 0x80000000
//
// The helper is a guest function of its own: the translated path calls it, so
// the instructions *it* retires are part of the count the guest sees.  The
// result therefore carries the number of instructions the translated helper
// retires on the same input and the value it leaves in f2.
struct GxFloatFixed {
  uint32_t mantissa;   // the helper's r3
  double f2;           // the helper's f2 (the fctiwz result)
  double c0, c1, c2;   // the helper's own constants (its f0, f3 and f4)
  GxCmp cr0;           // the helper's cr0 (the comparison with c0)
  GxCmp cr6;           // the helper's cr6 (the comparison with c1)
  GxCmp cr7;           // the helper's cr7 (the comparison with c2)
  bool wrote_f2;       // the helper reached its fctiwz
  bool wrote_cr6;      // the helper reached its second comparison
  bool wrote_cr7;      // the helper reached its third comparison
  uint32_t inst_count; // the instructions the translated helper retired
};

inline GxFloatFixed gx_float_to_fixed(GuestMem& mem, float value) {
  const double c0 = mem.read_f64(gx::kFloatToFixedTable + 0);
  const double c1 = mem.read_f64(gx::kFloatToFixedTable + 8);
  const double c2 = mem.read_f64(gx::kFloatToFixedTable + 16);
  const double v = static_cast<double>(value);
  GxFloatFixed out;
  out.mantissa = 0u;
  out.f2 = 0.0;
  out.c0 = c0;
  out.c1 = c1;
  out.c2 = c2;
  out.cr0 = gx_fcmpu(v, c0);
  out.cr6 = gx_fcmpu(v, c1);
  out.cr7 = gx_fcmpu(v, c2);
  out.wrote_f2 = false;
  out.wrote_cr6 = false;
  out.wrote_cr7 = false;
  // `fcmpu` is unordered for NaN: every relational test is false, so a NaN
  // takes the "not less than c0" and "not less than c1" branches.
  if (v < c0) {
    out.mantissa = 0u;
    out.inst_count = 11u;
    out.wrote_cr6 = true;   // the helper's second comparison runs before its
    return out;             // first branch
  }
  if (!(v < c1)) {
    out.mantissa = 0xFFFFFFFFu;
    out.inst_count = 13u;
    out.wrote_cr6 = true;
    return out;
  }
  const bool below = (v < c2);
  const double t = below ? v : (v - c2);
  const uint32_t i = gx_fctiwz(t);
  out.mantissa = below ? i : (i + 0x80000000u);
  // The guest's `fctiwz` leaves the *bit pattern* of the integer in f2.
  const uint64_t bits = i;
  std::memcpy(&out.f2, &bits, 8);
  out.wrote_f2 = true;
  out.wrote_cr6 = true;
  out.wrote_cr7 = true;
  out.inst_count = below ? 19u : 23u;
  return out;
}

// The PSQ pair load the guest performs with `psq_l frD, addr, 0, 0` and the
// default GQR (a single element load).  The runtime's fast path reads the
// element as a float and stores it in the double FPR, which quiets a
// signalling NaN; the matching `psq_st` converts it back to a float.  The
// lowering reproduces that round trip so the bytes that reach the write
// gather pipe are bit identical.
//
// `psq_load` also leaves the two loaded floats in the *register file*: the low
// element in `fpr[frD]` and the high element in `ps1[frD]` (both widened to
// double).  `psq_f32_raw_bits` returns the element's raw bits so a lowering
// can publish both halves; `psq_quiet_bits` applies the float->double->float
// round trip the store performs.
inline uint32_t psq_f32_raw_bits(GuestMem& mem, uint32_t addr) {
  return f32_bits(mem.read_f32(addr));
}

inline uint32_t psq_quiet_bits(uint32_t bits) {
  // float -> double -> float quiets a signalling NaN (the MSB of the
  // mantissa is set); a quiet NaN and every normal value are unchanged.
  if ((bits & 0x7F800000u) == 0x7F800000u && (bits & 0x007FFFFFu) != 0u) {
    bits |= 0x00400000u;
  }
  return bits;
}

inline uint32_t psq_f32_bits(GuestMem& mem, uint32_t addr) {
  return psq_quiet_bits(psq_f32_raw_bits(mem, addr));
}

// The guest's `psq_st frS, addr, 0, 0` with the default GQR: it writes the
// single element and the paired single element, both as floats.
//
// The pair access is a PSQ register operation, not a plain memory access:
// the differential harness deliberately does not record the guest's
// `ctx.psq_store(...)` calls.  The lowering therefore performs the same two
// MMU level float writes instead of going through the traced `GuestMem`, so
// that the ordered effect log of both paths stays identical.  The bytes that
// reach the write gather pipe are the same either way.
inline void psq_store_pair(GuestMem& mem, uint32_t addr, uint32_t lo_bits, uint32_t hi_bits) {
  mem.mmu.write_f32(addr, bits_f32(lo_bits));
  mem.mmu.write_f32(addr + 4, bits_f32(hi_bits));
}

// The guest's PSQ pair round trip through the stack converts the paired
// single register to a float and back.  GXSetFog spills f31/ps1[31] in its
// prologue and reloads them in its epilogue, so ps1[31] ends up as
// `(double)(float)(ps1[31])`.  The volatile stores force the two
// conversions so a signalling NaN is quieted exactly like the guest.
inline double psq_roundtrip_f32(double v) {
  volatile float f = static_cast<float>(v);
  volatile double d = static_cast<double>(f);
  return d;
}

// The guest's retired instruction count for GXSetVtxAttrFmt, measured from
// the translated body: the prologue, the switch dispatch, the case body and
// the epilogue.  `index` is the guest's `attr - 9`; the guest's bound check
// is an unsigned compare, so an index above 16 skips the table read and the
// indirect branch.
//
// The NRM case depends on the component count: `cmpwi r5, 2` with
// GX_NRM_NBT3 (2) falls through into a counted block instead of branching
// over it, so that path retires one more instruction (32, not 31).  The
// value update and the signed cr0 compare are handled in the case body.
inline uint32_t gx_vtx_attr_fmt_inst_count(uint32_t target, uint32_t index,
                                           uint32_t comp_cnt) {
  if (index > 16u) return 17u;
  switch (target) {
    case gx::kCasePos: return 28u;
    case gx::kCaseNrm:
      return (static_cast<int32_t>(comp_cnt) == 2) ? 32u : 31u;
    case gx::kCaseClr0: return 27u;
    case gx::kCaseClr1: return 27u;
    case gx::kCaseTex4: return 30u;
    case gx::kCaseDefault: return 23u;
    default: return 28u;
  }
}

// ---------------------------------------------------------------------------
// GXSetFog  (func_803CD2A0, 491 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXPixel.c :: GXSetFog
//
//   void GXSetFog(GXFogType type, GXColor color, f32 start, f32 end, f32 near, f32 far)
//
// Contract
//   inputs  : type (bits 0..2 = fsel, bit 3 = the projection flag), the colour
//             pointer and the four single precision values start, end, near
//             and far (the guest keeps them in fpr1..fpr4)
//   outputs : five BP register writes to 0xCC008000 (FOGPARAM0, FOGPARAM1,
//             FOGPARAM2, FOGPARAM3, FOGCOLOR) and the clear of the
//             `lastWriteWasXF` flag at gxdt+2
//   memory  : read the GX state pointer from sda-0x38E0, read the colour
//             (u32) and the FP constants from the SDA, write the five BP
//             registers and gxdt+2
//   polls   : when a poll is due, return true with entry state and poll
//             credit restored. The wrapper calls native_original_803CD2A0
//             directly, which builds the real guest frame before scheduling.
//             No partial native state is exposed to a callback or save state.
//   guest registers published : the whole state the translated body leaves
//             behind (fpr0..fpr6, ps1[31], cr0 and gpr0, gpr3..gpr10; fpr31,
//             gpr1 and gpr28..gpr31 keep their entry value, exactly like the
//             guest's save/restore of them)
//
// The two formulas
//
//   The guest's branch at 0x803CD2D8 selects on bit 3 of `type`:
//
//     bit 3 clear (0x803CD324): the perspective formula.  It computes
//         f28 = (far * near) / ((far - near) * (end - start))
//         f25 = far / (far - near)
//         c   = start / (end - start)
//       and normalises f25 into [0.5, 1) with two loops, so that the
//       mantissa 8388638.0f * f25 fits into the BP register's 24 bit field.
//
//     bit 3 set (0x803CD2DC): the orthographic formula
//         a = (1.0f / (end - start)) * (far - near)
//         c = (1.0f / (end - start)) * (start - near)
//
//   Both formulas are skipped when the fog is degenerate (far == near or
//   end == start): the guest then writes a = c = 0.  `fcmpu` is unordered, so
//   a NaN argument never compares equal and never makes the fog degenerate.
//
// The instruction count
//
//   `ctx.inst_count` is guest visible (with the default virtual clock
//   `CPUContext::read_timebase()` *is* `inst_count`), so the native path has
//   to retire exactly the number of guest instructions the translated path
//   retired for the same input.  The translated body counts its instructions
//   with `ctx.inst_count += N` markers; the numbers below are the sums of
//   those markers along the executed path:
//
//     the prologue (stwu .. stw r28)                          15
//     the branch at 0x803CD2D8                                 1 (only when not taken)
//     the perspective path:
//       the fcmpu of far and near                              1
//       the branch at 0x803CD328                               1 (only when not taken)
//       the fcmpu of end and start                              1
//       the branch at 0x803CD330                               1 (only when not taken)
//       the degenerate block (lfs, lfs, fmr)                    3
//       or the computation block (0x803CD344 .. 0x803CD35C)     7
//     the first normalisation loop:
//       the setup (lfs f1, li r28, lfd f0)                      3
//       every iteration (fcmpu, fmuls, addi)                    3
//       the exit branch                                         1
//     the second normalisation loop:
//       the setup (lfd f0, lfs f2, lfs f1)                      3
//       every iteration (fcmpu, mcrxr, rlwinm, fcmpu,           6
//                        fmuls, addi)
//       the exit                                                3 or 6
//     the tail (0x803CD3B0 .. 0x803CD410)                      25
//     the guest's float helper (0x80393518)                    11, 13, 19 or 23
//     the tail (0x803CD414 .. the blr)                         45
//
//   The orthographic path has neither loops nor helper:
//
//     far == near    15 + 1 + 1 + 3 + 45                     = 65
//     end == start   15 + 1 + 1 + 1 + 1 + 1 + 3 + 45         = 68
//     otherwise      15 + 1 + 1 + 1 + 1 + 9 + 45             = 73
//
//   The perspective path adds the two normalisation loops and the helper:
//
//     far == near    19 + 5 + 6 + 25 + H + 45
//     end == start   22 + 5 + 6 + 25 + H + 45
//     otherwise      25 + (5 + 3*N1) + (3 + 7*N2 + e) + 25 + H + 45
//
//   with N1 the number of halvings, N2 the number of doublings, e = 3 when the
//   second loop stops because f25 is not positive and e = 6 otherwise, and H
//   the helper's own count.
//
//   The helper's own count depends on the mantissa it is given, so it is part
//   of the count the guest sees: the native path inlines the helper and adds
//   the number of instructions the translated helper would have retired.
inline bool gx_set_fog(GuestMem mem, uint64_t& inst_count, uint32_t sda, uint32_t type,
                       uint32_t color_ptr, double start, double end, double near_, double far_,
                       CPUContext& ctx) {
  GuestPoll poll(ctx);

  const uint32_t fsel = type & 7u;
  const uint32_t ortho = (type >> 3) & 1u;   // the guest's r30

  // `fcmpu` is unordered for NaN, so a NaN never compares equal.
  const bool eq_far_near = (far_ == near_);
  const bool eq_end_start = (end == start);

  // The guest's floating point registers, mirrored.  Every result of the
  // guest's single precision arithmetic is rounded to a float, exactly like
  // the guest's `fmuls`, `fsubs` and `fdivs`.
  double F[7];
  F[0] = ctx.fpr[0];
  F[1] = start;
  F[2] = end;
  F[3] = near_;
  F[4] = far_;
  F[5] = ctx.fpr[5];
  F[6] = ctx.fpr[6];
  double f31 = ctx.fpr[31];
  // The guest's ps1[31] at two points in time: the prologue spills it into the
  // frame (and the epilogue reads the spill back), so the value the guest
  // leaves behind is the *entry* value rounded to a float.  The body itself
  // overwrites ps1[31] with the fog's C value, which a preemption would
  // expose.
  const double ps1_31_entry = ctx.ps1[31];
  double ps1_31 = ps1_31_entry;

  uint64_t count = 15;   // the prologue
  int32_t exp_b = 0;     // the guest's r28, the fog exponent

  float a = 0.0f;            // FOGPARAM0's A
  float c = 0.0f;            // FOGPARAM3's C
  uint32_t fog_param1 = 0u;  // FOGPARAM1 (B_MAG)
  uint32_t fog_param2 = 0u;  // FOGPARAM2 (B_SHIFT)
  GxCmp cr = {false, false, false, false};
  GxCmp cr6 = {false, false, false, false};
  GxCmp cr7 = {false, false, false, false};
  // The guest's float helper only runs on the perspective path; the ortho
  // path leaves cr6 and cr7 alone.  The helper writes cr6 on every path and
  // cr7 only when it reaches its third comparison.
  bool wrote_cr6 = false;
  bool wrote_cr7 = false;

  if (ortho) {
    // 0x803CD2DC .. 0x803CD320: the orthographic formula.
    count += 1;                       // the branch at 0x803CD2D8 is not taken
    count += 1;                       // the fcmpu of far and near
    cr = gx_fcmpu(F[4], F[3]);
    if (eq_far_near) {
      // The branch at 0x803CD2E0 is taken: the degenerate block.
      count += 3;                     // the lfs and the two stfs
      F[0] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogZeroF32)));
      a = 0.0f;
      c = 0.0f;
    } else {
      count += 1;                     // the branch at 0x803CD2E0 is not taken
      cr = gx_fcmpu(F[2], F[1]);
      count += 1;                     // the fcmpu of end and start
      if (eq_end_start) {
        count += 1;                   // the branch at 0x803CD2E8 is not taken
        count += 3;                   // the degenerate block
        F[0] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogZeroF32)));
        a = 0.0f;
        c = 0.0f;
      } else {
        count += 9;                   // the computation block
        F[6] = static_cast<float>(F[2] - F[1]);   // fsubs f6 = f2 - f1
        F[5] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogOneF32)));
        F[2] = static_cast<float>(F[4] - F[3]);   // fsubs f2 = f4 - f3
        F[0] = static_cast<float>(F[1] - F[3]);   // fsubs f0 = f1 - f3
        F[3] = static_cast<float>(F[5] / F[6]);   // fdivs f3 = f5 / f6
        F[1] = static_cast<float>(F[3] * F[2]);   // fmuls f1 = f3 * f2   (A)
        F[0] = static_cast<float>(F[3] * F[0]);   // fmuls f0 = f3 * f0   (C)
        a = static_cast<float>(F[1]);
        c = static_cast<float>(F[0]);
      }
    }
    count += 45;                      // the tail
  } else {
    // 0x803CD324 .. 0x803CD360: the perspective formula.
    count += 1;                       // the fcmpu of far and near
    cr = gx_fcmpu(F[4], F[3]);
    if (eq_far_near) {
      // The branch at 0x803CD328 is taken: the degenerate block.
      count += 3;                     // the lfs f3, the lfs f4 and the fmr f31
      F[3] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogZeroF32)));
      F[4] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogHalfF32)));
      f31 = F[3];
    } else {
      count += 1;                     // the branch at 0x803CD328 is not taken
      cr = gx_fcmpu(F[2], F[1]);
      count += 1;                     // the fcmpu of end and start
      if (eq_end_start) {
        count += 1;                   // the branch at 0x803CD330 is not taken
        count += 3;                   // the degenerate block
        F[3] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogZeroF32)));
        F[4] = static_cast<double>(mem.read_f32(sda + static_cast<uint32_t>(gx::kFogHalfF32)));
        f31 = F[3];
      } else {
        count += 7;                   // the computation block
        F[0] = static_cast<float>(F[4] - F[3]);   // fsubs f0 = f4 - f3
        F[2] = static_cast<float>(F[2] - F[1]);   // fsubs f2 = f2 - f1
        F[3] = static_cast<float>(F[4] * F[3]);   // fmuls f3 = f4 * f3
        F[4] = static_cast<float>(F[4] / F[0]);   // fdivs f4 = f4 / f0   (f25)
        F[0] = static_cast<float>(F[0] * F[2]);   // fmuls f0 = f0 * f2
        f31 = static_cast<float>(F[1] / F[2]);    // fdivs f31 = f1 / f2  (C)
        ps1_31 = f31;                            // the guest writes ps1[31] too
        F[3] = static_cast<float>(F[3] / F[0]);   // fdivs f3 = f3 / f0   (f28)
      }
    }

    // --- the first normalisation loop (0x803CD360) ----------------------
    //   The guest halves f25 until it is at most 1.0.  The loop is a
    //   pre-test loop: the body runs only while f25 > 1.0.
    const float half = mem.read_f32(sda + static_cast<uint32_t>(gx::kFogHalfF32));
    const double one = mem.read_f64(sda + static_cast<uint32_t>(gx::kFogOneF64));
    count += 3;                       // the setup: the lfs f1, the li r28, the lfd f0
    F[1] = static_cast<double>(half);
    F[0] = one;
    for (;;) {
      count += 1;                     // the fcmpu of f25 and 1.0
      cr = gx_fcmpu(F[4], one);
      if (poll.poll(0x803CD37C)) {    // the guest's poll point
        return true;
      }
      if (cr.gt) {
        count += 2;                   // the fmuls and the addi
        F[4] = static_cast<float>(F[4] * static_cast<double>(half));
        ++exp_b;
        continue;
      }
      count += 1;                     // the branch at 0x803CD37C
      break;
    }

    // --- the second normalisation loop (0x803CD380) ---------------------
    //   The guest doubles f25 until it is at least 0.5 and stops earlier when
    //   f25 is not positive.  The `mcrxr`/`rlwinm` pair turns the "f25 > 0.0"
    //   test into a branch on the equality of cr0.
    const double half_d = mem.read_f64(sda + static_cast<uint32_t>(gx::kFogHalfF64));
    const float two = mem.read_f32(sda + static_cast<uint32_t>(gx::kFogTwoF32));
    const float zero = mem.read_f32(sda + static_cast<uint32_t>(gx::kFogZeroF32));
    count += 3;                       // the setup: the lfd f0, the lfs f2, the lfs f1
    F[0] = half_d;
    F[2] = static_cast<double>(two);
    F[1] = static_cast<double>(zero);
    for (;;) {
      count += 3;                     // the fcmpu, the mcrxr and the rlwinm
      cr = gx_fcmpu(F[4], static_cast<double>(zero));
      if (!cr.gt) {
        // The branch at 0x803CD3A4 is taken: the `mcrxr` leaves cr0 with the
        // equality bit set and the "greater" bit cleared.
        cr.lt = false;
        cr.gt = false;
        cr.eq = true;
        break;
      }
      count += 1;                     // the branch at 0x803CD3A4 is not taken
      count += 1;                     // the fcmpu of f25 and 0.5
      const GxCmp half_cmp = gx_fcmpu(F[4], half_d);
      if (poll.poll(0x803CD3AC)) {    // the guest's poll point
        return true;
      }
      if (half_cmp.lt) {
        count += 2;                   // the fmuls and the addi
        F[4] = static_cast<float>(F[4] * static_cast<double>(two));
        --exp_b;
        continue;
      }
      count += 1;                     // the branch at 0x803CD3AC
      cr = half_cmp;
      break;
    }

    // --- the divisor, the mantissa and the guest's float helper ----------
    //   a = f28 / (1 << (expB + 1)).  The guest builds the divisor as a
    //   double: its high word is 0x80000000 | (1 << (expB + 1)) and its low
    //   word is 0x43300000; the difference against the SDA constant
    //   0x43300000_80000000 is exactly 2^(expB+1).
    const uint32_t r0 = static_cast<uint32_t>(exp_b + 1);
    uint32_t r3 = (r0 & 0x20u) ? 0u : (1u << (r0 & 0x1Fu));
    r3 ^= 0x80000000u;
    const uint64_t divisor_bits = (static_cast<uint64_t>(0x43300000u) << 32) | r3;
    double divisor;
    std::memcpy(&divisor, &divisor_bits, 8);
    const float mag_const = mem.read_f32(sda + static_cast<uint32_t>(gx::kFogMagF32));
    F[1] = static_cast<float>(static_cast<double>(mag_const) * F[4]);   // the mantissa
    const double k = mem.read_f64(sda + static_cast<uint32_t>(gx::kFogDivF64));
    F[2] = k;                           // the guest's lfd of the SDA constant
    F[0] = static_cast<float>(divisor - k);
    F[0] = static_cast<float>(F[3] / F[0]);
    a = static_cast<float>(F[0]);
    c = static_cast<float>(f31);
    count += 15;                      // 0x803CD3B0 .. 0x803CD3E8
    const GxFloatFixed fixed = gx_float_to_fixed(mem, static_cast<float>(F[1]));
    count += fixed.inst_count;        // the guest's float helper (0x80393518)
    F[0] = fixed.c0;                  // the helper's lfd of its first constant
    F[3] = fixed.c1;
    F[4] = fixed.c2;
    if (fixed.wrote_f2) F[2] = fixed.f2;   // the helper's fctiwz result
    cr = fixed.cr0;                   // the helper's cr0
    wrote_cr6 = fixed.wrote_cr6;      // the helper's cr6
    wrote_cr7 = fixed.wrote_cr7;      // the helper's cr7
    if (wrote_cr6) cr6 = fixed.cr6;
    if (wrote_cr7) cr7 = fixed.cr7;
    count += 10;                      // 0x803CD3EC .. 0x803CD410
    fog_param1 = (fixed.mantissa & 0x00FFFFFFu) | 0xEF000000u;  // GX_BP_REG_FOGPARAM1
    fog_param2 = (r0 & 0x1Fu) | 0xF0000000u;                     // GX_BP_REG_FOGPARAM2
    count += 45;                      // the tail
  }

  const uint32_t a_bits = f32_bits(a);
  const uint32_t c_bits = f32_bits(c);

  // FOGPARAM0: A mantissa (bits 0-10), A exponent (bits 11-18), A sign (19).
  uint32_t fog_param0 = (a_bits >> 12) & 0x7FFu;
  fog_param0 |= ((a_bits >> 23) & 0xFFu) << 11;
  fog_param0 |= ((a_bits >> 31) & 1u) << 19;
  fog_param0 |= 0xEE000000u;  // GX_BP_REG_FOGPARAM0

  // FOGPARAM3: C mantissa, C exponent, C sign, PROJ (bit 20) and FSEL (21-23).
  uint32_t fog_param3 = (c_bits >> 12) & 0x7FFu;
  fog_param3 |= ((c_bits >> 23) & 0xFFu) << 11;
  fog_param3 |= ((c_bits >> 31) & 1u) << 19;
  fog_param3 |= (ortho & 1u) << 20;
  fog_param3 |= (fsel & 7u) << 21;
  fog_param3 |= 0xF1000000u;  // GX_BP_REG_FOGPARAM3

  // FOGCOLOR: the colour's RGB in bits 0-23.  The guest reads the colour
  // between the FOGPARAM0 and the FOGPARAM1 write, and the state pointer
  // between the FOGPARAM1 and the FOGPARAM2 write; the reads are kept in that
  // order, so a watchpoint on them fires in the same order as on the
  // translated path.
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, fog_param0);
  const uint32_t color = mem.read32(color_ptr);
  uint32_t fog_color = (color >> 8) & 0x00FFFFFFu;
  fog_color |= 0xF2000000u;  // GX_BP_REG_FOGCOLOR
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, fog_param1);
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, fog_param2);
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, fog_param3);
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, fog_color);
  mem.write16(state + gx::kLastWriteWasXF, 0u);  // gxdt->lastWriteWasXF = FALSE

  // The guest's epilogue reloads the spilled ps1[31] through the PSQ pair
  // unit, which rounds it to single precision.  The spill holds the value
  // the prologue captured, not the value the body wrote.
  ctx.ps1[31] = psq_roundtrip_f32(ps1_31_entry);

  // The guest's register file after the call.  fpr31, gpr1 and gpr28..gpr31
  // keep their entry value (the guest saves and reloads them), so the native
  // path leaves them alone.
  ctx.fpr[0] = F[0];
  ctx.fpr[1] = F[1];
  ctx.fpr[2] = F[2];
  ctx.fpr[3] = F[3];
  ctx.fpr[4] = F[4];
  ctx.fpr[5] = F[5];
  ctx.fpr[6] = F[6];
  ctx.cr[0].lt = cr.lt;
  ctx.cr[0].gt = cr.gt;
  ctx.cr[0].eq = cr.eq;
  ctx.cr[0].so = cr.so;
  if (wrote_cr6) {
    ctx.cr[6].lt = cr6.lt;
    ctx.cr[6].gt = cr6.gt;
    ctx.cr[6].eq = cr6.eq;
    ctx.cr[6].so = cr6.so;
  }
  if (wrote_cr7) {
    ctx.cr[7].lt = cr7.lt;
    ctx.cr[7].gt = cr7.gt;
    ctx.cr[7].eq = cr7.eq;
    ctx.cr[7].so = cr7.so;
  }
  // The guest's epilogue reloads the saved link register into r0 and moves it
  // back into lr, so r0 holds the entry link register at the end.
  ctx.gpr[0] = ctx.lr;
  ctx.gpr[3] = state;
  ctx.gpr[4] = 0xCC010000u;
  ctx.gpr[5] = 97u;
  ctx.gpr[6] = fog_color;
  ctx.gpr[7] = fog_param3;
  ctx.gpr[8] = fog_param1;
  ctx.gpr[9] = fog_param2;
  ctx.gpr[10] = c_bits;

  inst_count += count;  // the guest's retired instruction count
  return false;
}

// ---------------------------------------------------------------------------
// GXClearVtxDesc  (func_803C9720, 23 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXAttr.c :: GXClearVtxDesc
//
//   void GXClearVtxDesc(void)
//
// Contract: vcdLoReg = GX_DIRECT in the POSITION field (bits 9-10, so
// 0x200), vcdHiReg = 0, normal = FALSE, binormal = FALSE and the VCD dirty
// flag (bit 3) is set.  Four state block writes plus the dirty flag.
inline void gx_clear_vtx_desc(GuestMem mem, uint64_t& inst_count, uint32_t sda) {
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  mem.write32(state + gx::kVcdLoReg, 0x200u);
  mem.write32(state + gx::kVcdHiReg, 0u);
  mem.write8(state + gx::kNormal, 0u);
  mem.write8(state + gx::kBinormal, 0u);
  const uint32_t dirty = mem.read32(state + gx::kDirtyFlags);
  mem.write32(state + gx::kDirtyFlags, dirty | 8u);
  inst_count += 13;  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXSetVtxAttrFmt  (func_803C9754, 115 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXAttr.c :: GXSetVtxAttrFmt
//
//   void GXSetVtxAttrFmt(GXVtxFmt fmt, GXAttr attr, GXCompCnt compCnt,
//                        GXCompType compType, u8 shift)
//
// The guest dispatches the attribute through a jump table that lives in the
// guest's data section at 0x80576578 and is indexed by `attr - 9`; the
// table has 17 entries and the bound check is an *unsigned* comparison
// (`cmplwi r0, 0x10` / `bgt`), so an attribute below 9 takes the default
// branch as well.  The table (read from orig/main.dol) is
//
//   index 0 (attr  9) -> POS   index  9 (attr 18) -> TEX5
//   index 1 (attr 10) -> NRM   index 10 (attr 19) -> TEX6
//   index 2 (attr 11) -> CLR0  index 11 (attr 20) -> TEX7
//   index 3 (attr 12) -> CLR1  index 12..15      -> default
//   index 4 (attr 13) -> TEX0  index 16 (attr 25) -> NRM
//   index 5 (attr 14) -> TEX1
//   index 6 (attr 15) -> TEX2
//   index 7 (attr 16) -> TEX3
//   index 8 (attr 17) -> TEX4
//
// (GX_VA_NBT is 25 in this guest - GXSetArray's `cmpwi r3, 25` / `li r3, 10`
// proves it - and the attribute numbers of CLR0..TEX7 are one lower than in
// the recovered ogws source, which uses a different GX_VA numbering.)
//
// The bit positions of every field were derived from the `rlwimi` masks of
// the translated case bodies; the mask of an `rlwimi` covers the value bits
// 31-ME .. 31-MB.
//
//   POS : vatA  cnt bit 0, type bits 1-3,  shift bits 4-8
//   NRM : vatA  type bits 10-12, cnt bit 9, normal-index-3 bit 31
//   CLR0: vatA  cnt bit 13, type bits 15-17
//   CLR1: vatA  cnt bit 14, type bits 18-20
//   TEX0: vatA  cnt bit 21, type bits 22-24, shift bits 25-29
//   TEX1: vatB  cnt bit 0,  type bits 1-3,   shift bits 4-8
//   TEX2: vatB  cnt bit 9,  type bits 10-12, shift bits 13-17
//   TEX3: vatB  cnt bit 18, type bits 19-21, shift bits 22-26
//   TEX4: vatB  cnt bit 27, type bits 28-30, shift in vatC bits 0-4
//   TEX5: vatC  cnt bit 5,  type bits 6-8,   shift bits 9-13
//   TEX6: vatC  cnt bit 14, type bits 15-17, shift bits 18-22
//   TEX7: vatC  cnt bit 23, type bits 24-26, shift bits 27-31
//
// The NRM case replaces the count with GX_NRM_NBT (1) and sets the
// normal-index-3 flag when the count is GX_NRM_NBT3 (2).
//
// Registers written by the translated body (and reproduced here)
//
//   The translated body touches no stack frame, so every register it writes
//   reaches the caller.  The values below were checked against the translated
//   body and against a probe run of it:
//
//     gpr0  the dirty-flag byte (gxdt+1531) OR the format's VAT dirty bit,
//           i.e. `(u8)(vatDirtyFlags | (1 << (fmt & 0x1F)))` (the guest's
//           rlwinm masks the shifted bit to 8 bits)
//     gpr3  the dirty-flag byte read from gxdt+1531
//     gpr4  the GX dirty-flags word read from gxdt+1532 (all paths; the
//           guest's jump-table target in r4 is overwritten there)
//     gpr5  the GX state block pointer (the guest re-reads sda-0x38E0 at
//           0x803C98C4)
//     gpr8  the state block plus the format's VAT slot, `state + (fmt<<2)`
//     cr0   the entry's unsigned `attr - 9` versus 16 comparison, except on
//           the NRM path where it is the signed `compCnt` versus 2 comparison
//     ctr   the jump-table target when `attr - 9 <= 16`, unchanged otherwise
//
//   `apply_gx_set_vtx_attr_fmt` publishes exactly those registers; the
//   differential test compares the whole register file.
struct VtxAttrFmtResult {
  uint32_t gpr0;
  uint32_t gpr3;
  uint32_t gpr4;
  uint32_t gpr5;
  uint32_t gpr8;
  bool cr_lt;
  bool cr_gt;
  bool cr_eq;
};

inline VtxAttrFmtResult gx_set_vtx_attr_fmt(GuestMem mem, uint64_t& inst_count, uint32_t sda,
                                            uint32_t fmt, uint32_t attr, uint32_t comp_cnt,
                                            uint32_t comp_type, uint32_t shift, uint32_t& ctr) {
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  const uint32_t base = state + (fmt << 2);  // the guest computes gxdt + fmt*4

  // The guest's `cmplwi r0, 0x10` is an unsigned compare, so an attribute
  // below GX_VA_POS takes the default branch as well.  The guest then reads
  // the jump table from the guest's data section and branches to the case
  // body; the lowering follows the same table so the branch target (and the
  // CTR the guest leaves behind) is identical.
  const uint32_t index = attr - gx::kAttrPos;
  uint32_t target = gx::kCaseDefault;
  if (index <= 16u) {
    target = mem.read32(gx::kVtxAttrFmtJumpTable + index * 4u);
    ctr = target;
  }
  switch (target) {
      case gx::kCasePos: {  // GX_VA_POS
        uint32_t v = mem.read32(base + gx::kVatA);
        v = (v & ~0x1u) | (comp_cnt & 1u);
        v = (v & ~0xEu) | ((comp_type & 7u) << 1);
        v = (v & ~0x1F0u) | ((shift & 0x1Fu) << 4);
        mem.write32(base + gx::kVatA, v);
        break;
      }
      case gx::kCaseNrm: {  // GX_VA_NRM and the guest's attribute 25
        // The guest updates vatA twice: once for the component type and once
        // for the count and the normal index flag.
        uint32_t v = mem.read32(base + gx::kVatA);
        v = (v & ~0x1C00u) | ((comp_type & 7u) << 10);
        mem.write32(base + gx::kVatA, v);
        v = mem.read32(base + gx::kVatA);
        if (static_cast<int32_t>(comp_cnt) == 2) {
          v |= 0x200u;        // cnt = GX_NRM_NBT
          v |= 0x80000000u;  // normal index 3
        } else {
          v = (v & ~0x200u) | ((comp_cnt & 1u) << 9);
          v &= 0x7FFFFFFFu;
        }
        mem.write32(base + gx::kVatA, v);
        break;
      }
      case gx::kCaseClr0: {  // GX_VA_CLR0
        uint32_t v = mem.read32(base + gx::kVatA);
        v = (v & ~0x2000u) | ((comp_cnt & 1u) << 13);
        v = (v & ~0x1C000u) | ((comp_type & 7u) << 14);
        mem.write32(base + gx::kVatA, v);
        break;
      }
      case gx::kCaseClr1: {  // GX_VA_CLR1
        uint32_t v = mem.read32(base + gx::kVatA);
        v = (v & ~0x20000u) | ((comp_cnt & 1u) << 17);
        v = (v & ~0x1C0000u) | ((comp_type & 7u) << 18);
        mem.write32(base + gx::kVatA, v);
        break;
      }
      case gx::kCaseTex0: {  // GX_VA_TEX0
        uint32_t v = mem.read32(base + gx::kVatA);
        v = (v & ~0x200000u) | ((comp_cnt & 1u) << 21);
        v = (v & ~0x1C00000u) | ((comp_type & 7u) << 22);
        v = (v & ~0x3E000000u) | ((shift & 0x1Fu) << 25);
        mem.write32(base + gx::kVatA, v);
        break;
      }
      case gx::kCaseTex1: {  // GX_VA_TEX1
        uint32_t v = mem.read32(base + gx::kVatB);
        v = (v & ~0x1u) | (comp_cnt & 1u);
        v = (v & ~0xEu) | ((comp_type & 7u) << 1);
        v = (v & ~0x1F0u) | ((shift & 0x1Fu) << 4);
        mem.write32(base + gx::kVatB, v);
        break;
      }
      case gx::kCaseTex2: {  // GX_VA_TEX2
        uint32_t v = mem.read32(base + gx::kVatB);
        v = (v & ~0x200u) | ((comp_cnt & 1u) << 9);
        v = (v & ~0x1C00u) | ((comp_type & 7u) << 10);
        v = (v & ~0x3E000u) | ((shift & 0x1Fu) << 13);
        mem.write32(base + gx::kVatB, v);
        break;
      }
      case gx::kCaseTex3: {  // GX_VA_TEX3
        uint32_t v = mem.read32(base + gx::kVatB);
        v = (v & ~0x40000u) | ((comp_cnt & 1u) << 18);
        v = (v & ~0x380000u) | ((comp_type & 7u) << 19);
        v = (v & ~0x7C00000u) | ((shift & 0x1Fu) << 22);
        mem.write32(base + gx::kVatB, v);
        break;
      }
      case gx::kCaseTex4: {  // GX_VA_TEX4
        uint32_t v = mem.read32(base + gx::kVatB);
        v = (v & ~0x8000000u) | ((comp_cnt & 1u) << 27);
        v = (v & ~0x70000000u) | ((comp_type & 7u) << 28);
        mem.write32(base + gx::kVatB, v);
        uint32_t w = mem.read32(base + gx::kVatC);
        w = (w & ~0x1Fu) | (shift & 0x1Fu);
        mem.write32(base + gx::kVatC, w);
        break;
      }
      case gx::kCaseTex5: {  // GX_VA_TEX5
        uint32_t v = mem.read32(base + gx::kVatC);
        v = (v & ~0x20u) | ((comp_cnt & 1u) << 5);
        v = (v & ~0x1C0u) | ((comp_type & 7u) << 6);
        v = (v & ~0x3E00u) | ((shift & 0x1Fu) << 9);
        mem.write32(base + gx::kVatC, v);
        break;
      }
      case gx::kCaseTex6: {  // GX_VA_TEX6
        uint32_t v = mem.read32(base + gx::kVatC);
        v = (v & ~0x4000u) | ((comp_cnt & 1u) << 14);
        v = (v & ~0x38000u) | ((comp_type & 7u) << 15);
        v = (v & ~0x7C0000u) | ((shift & 0x1Fu) << 18);
        mem.write32(base + gx::kVatC, v);
        break;
      }
      case gx::kCaseTex7: {  // GX_VA_TEX7
        uint32_t v = mem.read32(base + gx::kVatC);
        v = (v & ~0x800000u) | ((comp_cnt & 1u) << 23);
        v = (v & ~0x7000000u) | ((comp_type & 7u) << 24);
        v = (v & ~0xF8000000u) | ((shift & 0x1Fu) << 27);
        mem.write32(base + gx::kVatC, v);
        break;
      }
      default:
        break;  // the guest's default case
  }

  // gxDirtyFlags |= GX_DIRTY_VAT (0x10)
  const uint32_t dirty = mem.read32(state + gx::kDirtyFlags);
  mem.write32(state + gx::kDirtyFlags, dirty | 0x10u);
  // vatDirtyFlags |= (u8)(1 << (u8)fmt)
  const uint32_t fmt_bits = fmt & 0xFFu;
  const uint32_t flag = ((fmt_bits & 0x20u) ? 0u : (1u << (fmt_bits & 0x1Fu))) & 0xFFu;
  const uint32_t vat_dirty = mem.read8(state + gx::kVatDirtyFlags);
  mem.write8(state + gx::kVatDirtyFlags, static_cast<uint8_t>(vat_dirty | flag));
  inst_count += gx_vtx_attr_fmt_inst_count(target, index, comp_cnt);  // the guest's retired instruction count

  VtxAttrFmtResult r;
  r.gpr0 = vat_dirty | flag;       // 0x803C98E8: or r0, r3, r0 (both masked to 8 bits)
  r.gpr3 = vat_dirty;              // 0x803C98E4: lbz r3, 1531(r5)
  r.gpr4 = dirty;                  // 0x803C98D0: lwz r4, 1532(r5)
  r.gpr5 = state;                  // 0x803C98C4: lwz r5, -0x38E0(r2)
  r.gpr8 = base;                   // 0x803C9764: add r8, r8, r4 (the VAT slot)
  if (target == gx::kCaseNrm) {
    // 0x803C97A4: cmpwi r5, 2
    r.cr_lt = static_cast<int32_t>(comp_cnt) < 2;
    r.cr_gt = static_cast<int32_t>(comp_cnt) > 2;
    r.cr_eq = static_cast<int32_t>(comp_cnt) == 2;
  } else {
    // 0x803C975C: cmplwi r0, 0x10 (unsigned)
    r.cr_lt = index < 16u;
    r.cr_gt = index > 16u;
    r.cr_eq = index == 16u;
  }
  return r;
}

// Publishes the registers the translated body leaves modified.  The CR0
// summary-overflow bit is not written by the translated `cmpwi`/`cmplwi`
// lowering, so it keeps its entry value.
inline void apply_gx_set_vtx_attr_fmt(CPUContext& ctx) {
  const VtxAttrFmtResult r = gx_set_vtx_attr_fmt(
      guest_mem(ctx), ctx.inst_count, ctx.gpr[2], ctx.gpr[3], ctx.gpr[4], ctx.gpr[5], ctx.gpr[6],
      ctx.gpr[7], ctx.ctr);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.gpr[8] = r.gpr8;
  ctx.cr[0].lt = r.cr_lt;
  ctx.cr[0].gt = r.cr_gt;
  ctx.cr[0].eq = r.cr_eq;
  ctx.pc = ctx.lr;
}

// ---------------------------------------------------------------------------
// GXSetArray  (func_803C9B38, 21 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXAttr.c :: GXSetArray
//
//   void GXSetArray(GXAttr attr, const void* base, u8 stride)
//
// The guest converts GX_VA_NBT (25) to GX_VA_NRM (10) with a *signed*
// compare (`cmpwi r3, 25`) and then writes two CP registers:
// SETMODE0 (index | 0xA0) with the array address and IMAGE2 (index | 0xB0)
// with the stride.  The address is masked to 30 bits.
inline void gx_set_array(GuestMem mem, uint64_t& inst_count, uint32_t attr, uint32_t base,
                         uint32_t stride) {
  uint32_t a = attr;
  const bool was_nbt = (static_cast<int32_t>(a) == static_cast<int32_t>(gx::kAttrNbt));
  if (was_nbt) {
    a = gx::kAttrNrm;
  }
  const uint32_t index = a - gx::kAttrPos;
  mem.write8(gx::kBpPort, gx::kCpLoadTag);
  mem.write8(gx::kBpPort, static_cast<uint8_t>(index | 0xA0u));
  mem.write32(gx::kBpPort, base & 0x3FFFFFFFu);
  mem.write8(gx::kBpPort, gx::kCpLoadTag);
  mem.write8(gx::kBpPort, static_cast<uint8_t>(index | 0xB0u));
  mem.write32(gx::kBpPort, stride);
  inst_count += (was_nbt ? 16u : 14u);  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXSetTexCoordGen2  (func_803C9B88, 60 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXAttr.c :: GXSetTexCoordGen2
//
//   void GXSetTexCoordGen2(GXTexCoordID id, GXTexGenType type, GXTexGenSrc src,
//                          u32 texMtxIdx, GXBool normalize, u32 dualTexMtxIdx)
//
// The guest folds the six arguments into three switches and two GX state
// arrays:
//
//   * a 21-entry `src` table at 0x8057661C.  The guest bounds it *above* only
//     (`cmpwi r5, 20; bgt`), so a negative `src` reads below the table; the
//     entry guard therefore requires `(int32_t)src >= 0`.
//     The table selects the input row and the input form:
//       src  0..3   ABC1 form (bit 2), rows 0, 1, 3, 4
//       src  4..11  AB11 form, rows 5..12
//       src 12..18  the default block: AB11 form, row 5
//       src 19, 20  AB11 form, row 2
//       src > 20    the default block (the table is not read)
//   * a seven-entry `id` table at 0x80576600.  The guest bounds it above only
//     (`cmpwi r3, 6; bgt`), so the entry guard requires `(int32_t)id >= 0`;
//     ids above 6 take the default block (matrixIndex1 bits 18-23).
//     ids 0..3 write matrixIndex0 (gxdt+128) bits 6-11, 12-17, 18-23, 24-29;
//     ids 4..6 write matrixIndex1 (gxdt+132) bits 0-5, 6-11, 12-17; the
//     default writes matrixIndex1 bits 18-23.
//   * the `type` dispatch (an if-chain, not a table):
//       type < 0     reg = 0
//       type == 0    reg = 2 | inputForm<<2 | inputRow<<7   (2x4 projection)
//       type == 1    reg =         inputForm<<2 | inputRow<<7  (3x4)
//       type 2..9    the eight BUMP types:
//                    reg = inputForm<<2 | 1<<4 | inputRow<<7 |
//                          ((src-12)&7)<<12 | ((type-2)&7)<<15
//       type == 10   SRTG: reg = inputForm<<2 | (src==19 ? 2 : 3)<<4 | 2<<7
//       type > 10    reg = 0
//
// Memory effects, in this order (the differential test compares the ordered
// write log, so the order is part of the contract):
//
//   1. write32(gxdt + 200 + id*4, texRegs value)
//   2. write32(gxdt + 1532, dirty | (0x10000 << (id & 31)))       [teximg dirty]
//   3. write32(gxdt + 232 + id*4, dualTexRegs value)
//   4. read+write32(gxdt + 128 or 132, the matrix index field)
//   5. write32(gxdt + 1532, the value from (2) | 0x04000000)      [mtx idx dirty]
//
// dualTexRegs value = ((dualTexMtxIdx - 64) & 0x3F) | (normalize & 1) << 8.
//
// Retired instruction count
//
//   The translated body counts with `++`/`+=` markers, and the translator
//   leaves some of them after an unconditional `goto` where they can never
//   execute.  The counts below were therefore *measured* from the translated
//   body (a probe run of the extracted function), not inferred from the PPC
//   instruction stream.  The measured decomposition is:
//
//     entry                                                  1
//     prologue (cmpwi, li r11, li r12, li r10)                4
//     the `src <= 20` branch, when not taken                  1
//     the src table load (lis, rlwinm, addi, lwzx, mtctr)     5
//     the src case body   ABC1 cases 2, rows/colours 1, default 0
//     the `type == 1` compare                                 1
//     the `type != 1` branch, when not taken                  1
//     the type case body (see gx_tex_coord_type_inst_count)
//     the 0x803C9CD0 load of the state pointer                1
//     the 0x803C9CD4 block (rlwinm, addi, li, add, lis)       5
//     the 0x803C9CE8 block (9 state writes/ALU markers)       9
//     the `id <= 6` branch, when not taken                    1
//     the id table load (lis, addi, lwzx, mtctr)              4
//     the id case body (lwz, rlwimi, stw)                     3
//     the default id body (lwz, rlwimi, stw + marker)         3
//     the 0x803C9DA0 block (lwz, oris, stw)                   3
//
//   which is `5 + (src <= 20 ? 6 : 0) + srcCase + typeCase + (id <= 6 ? 26 : 21)`.
//
// Registers written by the translated body (and reproduced here)
//
//   gpr0   the second dirty word read at 0x803C9DA0, OR 0x04000000
//   gpr3   the id table target for id <= 6, otherwise `id`
//   gpr4   the dualTexRegs value above
//   gpr5   the original dirty word (the r5 the guest reads at 0x803C9CF8)
//   gpr8   state + ((id << 2) & ~3)
//   gpr9   (id << 2) & ~3
//   gpr10  the GX state block pointer
//   gpr11  the texRegs value
//   gpr12  the input form (0 or 1)
//   cr0    the signed `id` versus 6 comparison (the last compare on every path)
//   ctr    the id table target when id <= 6, else the src table target when
//          src <= 20, else unchanged
//
// The entry guard `(int32_t)ctx.gpr[3] >= 0 && ctx.gpr[3] < 8 &&
// (int32_t)ctx.gpr[5] >= 0` keeps the two wild-input cases - which read below
// a jump table and branch into another function's interior entries - on the
// translated body, and bounds the texRegs/dualTexRegs writes to the state
// block.
inline uint32_t gx_tex_coord_src_inst_count(uint32_t target) {
  switch (target) {
    case gx::kTexCaseSrcPos:
    case gx::kTexCaseSrcNrm:
    case gx::kTexCaseSrcBinrm:
    case gx::kTexCaseSrcTangent:
      return 2u;
    case gx::kTexCaseSrcColor:
    case gx::kTexCaseSrcColor1:
    case gx::kTexCaseSrcRow5:
    case gx::kTexCaseSrcRow6:
    case gx::kTexCaseSrcRow7:
    case gx::kTexCaseSrcRow8:
    case gx::kTexCaseSrcRow9:
    case gx::kTexCaseSrcRow10:
    case gx::kTexCaseSrcRow11:
    case gx::kTexCaseSrcRow12:
      return 1u;
    default:
      return 0u;
  }
}

// The type dispatch includes the 0x803C9C30 `cmpwi` marker and the
// `type != 1` branch marker; the SRTG body is one marker longer when
// `src == 19` (the not-taken branch at 0x803C9CB0).
inline uint32_t gx_tex_coord_type_inst_count(uint32_t type, uint32_t src) {
  if (type == 1u) return 4u;
  if (type == 0u) return 8u;
  if (static_cast<int32_t>(type) < 0) return 5u;
  if (type <= 9u) return 14u;
  if (type == 10u) return (src == 19u) ? 11u : 10u;
  return 4u;  // type > 10
}

// The input-domain guard.  `GXTexCoordID` is 0..7 and `GXTexGenSrc` is
// non-negative; anything else reads below a jump table (and so branches into
// another function's interior entries) or writes past the texRegs arrays, so
// the staged entry must keep it on the translated body.
inline bool gx_set_tex_coord_gen2_serves(uint32_t id, uint32_t src) {
  return static_cast<int32_t>(id) >= 0 && id < 8u && static_cast<int32_t>(src) >= 0;
}

struct SetTexCoordGen2Result {
  uint32_t gpr0;
  uint32_t gpr3;
  uint32_t gpr4;
  uint32_t gpr5;
  uint32_t gpr8;
  uint32_t gpr9;
  uint32_t gpr10;
  uint32_t gpr11;
  uint32_t gpr12;
  bool cr_lt;
  bool cr_gt;
  bool cr_eq;
};

inline SetTexCoordGen2Result gx_set_tex_coord_gen2(GuestMem mem, uint64_t& inst_count,
                                                    uint32_t sda, uint32_t id, uint32_t type,
                                                    uint32_t src, uint32_t tex_mtx,
                                                    uint32_t normalize, uint32_t dual_mtx,
                                                    uint32_t& ctr) {
  uint32_t input_row = 5;
  uint32_t input_form = 0;
  uint32_t src_case = 0;
  uint32_t count = 5u;  // entry + the four prologue instructions

  // --- the src switch ---------------------------------------------------
  // The guest's `cmpwi r5, 20` is signed; the guard keeps src non-negative,
  // so `src <= 20` is the table path.
  uint32_t src_target = gx::kTexCaseSrcDefault;
  if (static_cast<int32_t>(src) <= 20) {
    src_target = mem.read32(gx::kTexCoordSrcJumpTable + ((src << 2) & 0xFFFFFFFCu));
    ctr = src_target;  // 0x803C9BAC: mtctr r9
    count += 6u;       // the not-taken branch and the five dispatch instructions
    src_case = gx_tex_coord_src_inst_count(src_target);
    switch (src_target) {
      case gx::kTexCaseSrcPos:      input_row = 0; input_form = 1; break;
      case gx::kTexCaseSrcNrm:      input_row = 1; input_form = 1; break;
      case gx::kTexCaseSrcBinrm:    input_row = 3; input_form = 1; break;
      case gx::kTexCaseSrcTangent:  input_row = 4; input_form = 1; break;
      case gx::kTexCaseSrcColor:
      case gx::kTexCaseSrcColor1:   input_row = 2; break;
      case gx::kTexCaseSrcRow5:     input_row = 5; break;
      case gx::kTexCaseSrcRow6:     input_row = 6; break;
      case gx::kTexCaseSrcRow7:     input_row = 7; break;
      case gx::kTexCaseSrcRow8:     input_row = 8; break;
      case gx::kTexCaseSrcRow9:     input_row = 9; break;
      case gx::kTexCaseSrcRow10:    input_row = 10; break;
      case gx::kTexCaseSrcRow11:    input_row = 11; break;
      case gx::kTexCaseSrcRow12:    input_row = 12; break;
      default: break;  // the default block keeps row 5, form AB11
    }
  }
  count += src_case;

  // --- the type dispatch ------------------------------------------------
  uint32_t texreg = 0;
  if (type == 1u) {
    texreg = (input_form << 2) | (input_row << 7);
  } else if (type == 0u) {
    texreg = 2u | (input_form << 2) | (input_row << 7);
  } else if (static_cast<int32_t>(type) < 0) {
    texreg = 0u;
  } else if (type <= 9u) {
    texreg = (input_form << 2) | (1u << 4) | (input_row << 7) |
             (((src - 12u) & 7u) << 12) | (((type - 2u) & 7u) << 15);
  } else if (type == 10u) {
    texreg = (input_form << 2) | (((src == 19u) ? 2u : 3u) << 4) | (2u << 7);
  } else {
    texreg = 0u;
  }
  count += gx_tex_coord_type_inst_count(type, src);

  // --- the state writes -------------------------------------------------
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  const uint32_t scaled = (id << 2) & 0xFFFFFFFCu;
  const uint32_t slot = state + scaled;
  const uint32_t dual = ((dual_mtx - 64u) & 0x3Fu) | ((normalize & 1u) << 8);
  const uint32_t id_bit = ((id & 0x20u) != 0u) ? 0u : (0x10000u << (id & 31u));
  const uint32_t dirty = mem.read32(state + gx::kDirtyFlags);

  mem.write32(slot + gx::kTexRegs, texreg);
  mem.write32(state + gx::kDirtyFlags, dirty | id_bit);
  mem.write32(slot + gx::kDualTexRegs, dual);

  // --- the id switch ----------------------------------------------------
  count += 1u + 5u + 9u;  // 0x803C9CD0, the 0x803C9CD4 block, the 0x803C9CE8 block
  uint32_t gpr3 = id;
  if (static_cast<int32_t>(id) <= 6) {
    const uint32_t target = mem.read32(gx::kTexCoordIdJumpTable + scaled);
    ctr = target;  // 0x803C9D1C: mtctr r3
    gpr3 = target;
    count += 11u;  // branch, four dispatch instructions, case body, 0x803C9DA0 block
    uint32_t reg;
    switch (target) {
      case gx::kTexCaseId0:
        reg = mem.read32(state + gx::kMatrixIndex0);
        reg = (reg & ~0xFC0u) | ((tex_mtx & 0x3Fu) << 6);
        mem.write32(state + gx::kMatrixIndex0, reg);
        break;
      case gx::kTexCaseId1:
        reg = mem.read32(state + gx::kMatrixIndex0);
        reg = (reg & ~0x3F000u) | ((tex_mtx & 0x3Fu) << 12);
        mem.write32(state + gx::kMatrixIndex0, reg);
        break;
      case gx::kTexCaseId2:
        reg = mem.read32(state + gx::kMatrixIndex0);
        reg = (reg & ~0xFC0000u) | ((tex_mtx & 0x3Fu) << 18);
        mem.write32(state + gx::kMatrixIndex0, reg);
        break;
      case gx::kTexCaseId3:
        reg = mem.read32(state + gx::kMatrixIndex0);
        reg = (reg & ~0x3F000000u) | ((tex_mtx & 0x3Fu) << 24);
        mem.write32(state + gx::kMatrixIndex0, reg);
        break;
      case gx::kTexCaseId4:
        reg = mem.read32(state + gx::kMatrixIndex1);
        reg = (reg & ~0x3Fu) | (tex_mtx & 0x3Fu);
        mem.write32(state + gx::kMatrixIndex1, reg);
        break;
      case gx::kTexCaseId5:
        reg = mem.read32(state + gx::kMatrixIndex1);
        reg = (reg & ~0xFC0u) | ((tex_mtx & 0x3Fu) << 6);
        mem.write32(state + gx::kMatrixIndex1, reg);
        break;
      case gx::kTexCaseId6:
        reg = mem.read32(state + gx::kMatrixIndex1);
        reg = (reg & ~0x3F000u) | ((tex_mtx & 0x3Fu) << 12);
        mem.write32(state + gx::kMatrixIndex1, reg);
        break;
      case gx::kTexCaseIdDefault:
        reg = mem.read32(state + gx::kMatrixIndex1);
        reg = (reg & ~0xFC0000u) | ((tex_mtx & 0x3Fu) << 18);
        mem.write32(state + gx::kMatrixIndex1, reg);
        break;
      default:
        break;
    }
  } else {
    count += 6u;  // the taken branch, the default body and the 0x803C9DA0 block
    uint32_t reg = mem.read32(state + gx::kMatrixIndex1);
    reg = (reg & ~0xFC0000u) | ((tex_mtx & 0x3Fu) << 18);
    mem.write32(state + gx::kMatrixIndex1, reg);
  }

  // 0x803C9DA0: gxDirtyFlags |= GX_DIRTY_MTX_IDX (0x04000000)
  const uint32_t dirty2 = mem.read32(state + gx::kDirtyFlags);
  mem.write32(state + gx::kDirtyFlags, dirty2 | 0x04000000u);

  inst_count += count;  // the guest's retired instruction count

  SetTexCoordGen2Result r;
  r.gpr0 = dirty2 | 0x04000000u;  // 0x803C9DA4: oris r0, r0, 0x0400
  r.gpr3 = gpr3;                  // the id table target, or the id above 6
  r.gpr4 = dual;                  // 0x803C9D08: stw r4, 232(r8)
  r.gpr5 = dirty;                 // 0x803C9CF8: lwz r5, 1532(r10)
  r.gpr8 = slot;                  // 0x803C9CE0: add r8, r10, r9
  r.gpr9 = scaled;                // 0x803C9CD4: rlwinm r9, r3, 2, 0, 29
  r.gpr10 = state;                // 0x803C9CD0: lwz r10, -0x38E0(r2)
  r.gpr11 = texreg;               // 0x803C9CE8: stw r11, 200(r8)
  r.gpr12 = input_form;           // the src case's li r12, 0/1
  // 0x803C9CF4: cmpwi r3, 6
  r.cr_lt = static_cast<int32_t>(id) < 6;
  r.cr_gt = static_cast<int32_t>(id) > 6;
  r.cr_eq = static_cast<int32_t>(id) == 6;
  return r;
}

inline void apply_gx_set_tex_coord_gen2(CPUContext& ctx) {
  const SetTexCoordGen2Result r = gx_set_tex_coord_gen2(
      guest_mem(ctx), ctx.inst_count, ctx.gpr[2], ctx.gpr[3], ctx.gpr[4], ctx.gpr[5], ctx.gpr[6],
      ctx.gpr[7], ctx.gpr[8], ctx.ctr);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.gpr[8] = r.gpr8;
  ctx.gpr[9] = r.gpr9;
  ctx.gpr[10] = r.gpr10;
  ctx.gpr[11] = r.gpr11;
  ctx.gpr[12] = r.gpr12;
  ctx.cr[0].lt = r.cr_lt;
  ctx.cr[0].gt = r.cr_gt;
  ctx.cr[0].eq = r.cr_eq;
  ctx.pc = ctx.lr;
}

// ---------------------------------------------------------------------------
// GXSetZMode  (func_803CD698, 27 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXPixel.c :: GXSetZMode
//
//   void GXSetZMode(GXBool enableTest, GXCompare func, GXBool enableUpdate)
//
// The guest keeps the ZMode register in the state block (offset 552) and
// updates it in place: test enable in bit 0, compare function in bits 1-3 and
// update enable in bit 4.
inline void gx_set_z_mode(GuestMem mem, uint64_t& inst_count, uint32_t sda, uint32_t enable_test,
                          uint32_t func, uint32_t enable_update) {
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  uint32_t z_mode = mem.read32(state + gx::kZMode);
  z_mode = (z_mode & ~0x1u) | (enable_test & 1u);
  z_mode = (z_mode & ~0xEu) | ((func & 7u) << 1);
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  z_mode = (z_mode & ~0x10u) | ((enable_update & 1u) << 4);
  mem.write32(gx::kBpPort, z_mode);
  mem.write32(state + gx::kZMode, z_mode);
  mem.write16(state + gx::kLastWriteWasXF, 0u);  // gxdt->lastWriteWasXF = FALSE
  inst_count += 13;  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXSetChanCtrl  (func_803CB7E0, 37 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/petari/RVL_SDK/gx/GXLight.c :: GXSetChanCtrl
//
//   void GXSetChanCtrl(GXChannelID chan, GXBool enable, GXColorSrc amb_src,
//                       GXColorSrc mat_src, u32 light_mask, GXDiffuseFn diff_fn,
//                       GXAttnFn attn_fn)
//
// The guest builds the colour control register from scratch and stores it in
// gxdt->chanCtrl[chan & 3] (offset 184).  The bit positions were derived
// from the `rlwimi` masks of the translated body:
//
//   bit 0      material source   (argument 4)
//   bit 1      light function    (argument 2, `enable`)
//   bit 6      ambient source    (argument 3)
//   bits 7-8   diffuse attenuation
//   bit 9      attenuation select (0 when attn_fn == GX_AF_SPEC)
//   bit 10     attenuation enable (0 when attn_fn == GX_AF_NONE)
//   bits 2-5   light 0-3 mask
//   bits 11-14 light 4-7 mask
//
// The guest zeroes the diffuse function when attn_fn is GX_AF_NONE (0) and
// clears the attenuation select bit when attn_fn is GX_AF_SPEC (2); the
// `subfic` also updates the XER carry, which the guest's caller may read, so
// the carry is written back through the `xer` argument.
inline void gx_set_chan_ctrl(GuestMem mem, uint64_t& inst_count, uint32_t sda, uint32_t chan,
                             uint32_t enable, uint32_t amb_src, uint32_t mat_src,
                             uint32_t light_mask, uint32_t diff_fn, uint32_t attn_fn,
                             uint32_t& xer) {
  const uint32_t index = chan & 3u;
  uint32_t reg = 0u;
  reg = (reg & ~0x2u) | ((enable & 1u) << 1);
  reg = (reg & ~0x1u) | (mat_src & 1u);
  reg = (reg & ~0x40u) | ((amb_src & 1u) << 6);

  // The guest's `subfic r5, r9, 2` (r9 = attn_fn): r5 = 2 - attn_fn and the
  // carry is set when 2 >= attn_fn.
  const uint32_t two_minus = 2u - attn_fn;
  const uint32_t attn_minus = attn_fn - 2u;
  const uint32_t sel = two_minus | attn_minus;  // 0 for SPEC, else all ones
  xer = (xer & ~(1u << 29)) | ((2u >= attn_fn) ? (1u << 29) : 0u);

  const uint32_t diff = (attn_fn == 0u) ? 0u : diff_fn;
  reg = (reg & ~0x180u) | ((diff & 3u) << 7);
  reg = (reg & ~0x200u) | (((sel >> 31) & 1u) << 9);
  reg = (reg & ~0x400u) | (((((0u - attn_fn) | attn_fn) >> 31) & 1u) << 10);
  reg = (reg & ~0x3Cu) | ((light_mask & 0xFu) << 2);
  reg = (reg & ~0x7800u) | (((light_mask >> 4) & 0xFu) << 11);

  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  mem.write32(state + gx::kChanCtrl + index * 4u, reg);

  const uint32_t dirty = mem.read32(state + gx::kDirtyFlags);
  mem.write32(state + gx::kDirtyFlags, dirty | (0x1000u << index));

  if (static_cast<int32_t>(chan) == 4) {  // GX_COLOR0A0
    mem.write32(state + gx::kChanCtrl + 2u * 4u, reg);
    const uint32_t d2 = mem.read32(state + gx::kDirtyFlags);
    mem.write32(state + gx::kDirtyFlags, d2 | 0x5000u);
  } else if (static_cast<int32_t>(chan) == 5) {  // GX_COLOR1A1
    mem.write32(state + gx::kChanCtrl + 3u * 4u, reg);
    const uint32_t d2 = mem.read32(state + gx::kDirtyFlags);
    mem.write32(state + gx::kDirtyFlags, d2 | 0xA000u);
  }
  inst_count += (chan == 4u ? 33u : (chan == 5u ? 33u : (chan == 0u ? 29u : 28u))) + (attn_fn == 0u ? 1u : 0u);  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXSetFogRangeAdj  (func_803CD4CC, 29 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXPixel.c :: GXSetFogRangeAdj
//
//   void GXSetFogRangeAdj(GXBool enable, u16 center, const GXFogAdjTable* table)
//
// The guest writes the five FOGRANGEK registers (opcode 0xE9..0xED) when
// `enable` is set, each one holding the high 12 bits of table->r[2i] in bits
// 0-11 and the high 12 bits of table->r[2i+1] in bits 12-23.  The FOGRANGE
// register (opcode 0xE8) holds `center + 342` in bits 0-9 and the enable bit
// in bit 10.
//
// Registers written by the translated body (and reproduced here)
//
//   The translated body has two return paths.  `enable == 0` skips the ten
//   FOGRANGEK register writes and leaves gpr7..gpr12 untouched; the
//   `enable != 0` path writes them and leaves:
//
//     gpr0  0
//     gpr3  the GX state block pointer (sda-0x38E0)
//     gpr4  0xCC010000 (the BP port with the -0x8000 displacement not applied)
//     gpr5  0xE8, the FOGRANGE opcode
//     gpr6  0xE8000000 | (center + 342) & 0x3FF | (enable & 1) << 10
//     gpr7  0xED, the last FOGRANGEK opcode (enable != 0 only)
//     gpr8  0xED000000 | ((table[16] & 0xFFF) | ((table[18] & 0xFFF) << 12))
//     gpr9  ((table[16] & 0xFFF) | ((table[18] & 0xFFF) << 12))
//     gpr10 the last `lhz`, table[18] (a full 16-bit value)
//     gpr11 table[16] & 0xFFF
//     gpr12 the last `lhz` into r12, table[16] (a full 16-bit value)
//     cr0   the signed `enable` versus 0 comparison
//
//   (`table[16]`/`table[18]` are the two halves of the fifth GXFogAdjTable
//   entry, i.e. the last loop iteration.)
struct FogRangeAdjResult {
  uint32_t gpr0;
  uint32_t gpr3;
  uint32_t gpr4;
  uint32_t gpr5;
  uint32_t gpr6;
  uint32_t gpr7;
  uint32_t gpr8;
  uint32_t gpr9;
  uint32_t gpr10;
  uint32_t gpr11;
  uint32_t gpr12;
  bool wrote_table_regs;  // the `enable != 0` path: gpr7..gpr12 are valid
  bool cr_lt;
  bool cr_gt;
  bool cr_eq;
};

inline FogRangeAdjResult gx_set_fog_range_adj(GuestMem mem, uint64_t& inst_count, uint32_t sda,
                                              uint32_t enable, uint32_t center, uint32_t table) {
  uint32_t hi_last = 0;
  uint32_t lo_last = 0;
  if (enable != 0u) {
    for (uint32_t i = 0; i < 5u; ++i) {
      const uint32_t hi = mem.read16(table + 4u * i);
      const uint32_t lo = mem.read16(table + 4u * i + 2u);
      const uint32_t reg = (hi & 0xFFFu) | ((lo & 0xFFFu) << 12) | ((0xE9u + i) << 24);
      mem.write8(gx::kBpPort, gx::kBpLoadTag);
      mem.write32(gx::kBpPort, reg);
      hi_last = hi;
      lo_last = lo;
    }
  }
  uint32_t reg = (center + 342u) & 0x3FFu;
  reg |= (enable & 1u) << 10;
  reg |= 0xE8000000u;  // GX_BP_REG_FOGRANGE
  mem.write8(gx::kBpPort, gx::kBpLoadTag);
  mem.write32(gx::kBpPort, reg);
  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  mem.write16(state + gx::kLastWriteWasXF, 0u);  // gxdt->lastWriteWasXF = FALSE
  inst_count += (enable != 0u ? 73u : 15u);  // the guest's retired instruction count

  FogRangeAdjResult r;
  r.gpr0 = 0;                // 0x803CD5E4: li r0, 0
  r.gpr3 = state;            // 0x803CD5DC: lwz r3, -0x38E0(r2)
  r.gpr4 = 0xCC010000u;      // 0x803CD5C4: lis r4, 0xCC01
  r.gpr5 = 0xE8u;            // 0x803CD5CC: li r5, 0xE8
  r.gpr6 = reg;              // the complete FOGRANGE register
  r.gpr7 = 0xEDu;            // 0x803CD5A0: li r7, 0xED
  r.gpr8 = ((0xEDu << 24) | (hi_last & 0xFFFu) | ((lo_last & 0xFFFu) << 12));
  r.gpr9 = (hi_last & 0xFFFu) | ((lo_last & 0xFFFu) << 12);
  r.gpr10 = lo_last;         // 0x803CD590: lhz r10, 18(r5)
  r.gpr11 = hi_last & 0xFFFu;
  r.gpr12 = hi_last;         // 0x803CD580: lhz r12, 16(r5)
  r.wrote_table_regs = (enable != 0u);
  r.cr_lt = static_cast<int32_t>(enable) < 0;
  r.cr_gt = static_cast<int32_t>(enable) > 0;
  r.cr_eq = static_cast<int32_t>(enable) == 0;
  return r;
}

// Publishes exactly the registers the translated body leaves modified; the
// `enable == 0` path keeps gpr7..gpr12 (and the whole register file above
// cr0's three bits) at their entry value.
inline void apply_gx_set_fog_range_adj(CPUContext& ctx) {
  const FogRangeAdjResult r =
      gx_set_fog_range_adj(guest_mem(ctx), ctx.inst_count, ctx.gpr[2], ctx.gpr[3], ctx.gpr[4],
                           ctx.gpr[5]);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.gpr[6] = r.gpr6;
  if (r.wrote_table_regs) {
    ctx.gpr[7] = r.gpr7;
    ctx.gpr[8] = r.gpr8;
    ctx.gpr[9] = r.gpr9;
    ctx.gpr[10] = r.gpr10;
    ctx.gpr[11] = r.gpr11;
    ctx.gpr[12] = r.gpr12;
  }
  ctx.cr[0].lt = r.cr_lt;
  ctx.cr[0].gt = r.cr_gt;
  ctx.cr[0].eq = r.cr_eq;
  ctx.pc = ctx.lr;
}

// ---------------------------------------------------------------------------
// GXLoadPosMtxImm  (func_803CDA4C, 29 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXTransform.c :: GXLoadPosMtxImm
//
// The guest sends a CP "load XF registers" command for the position matrix
// and then copies the twelve matrix floats into the XF with six PSQ pair
// stores.  The XF address is `(idx << 2) | 0xB0000`.
//
// The PSQ pair accesses need the guest's GQR0 to be the default (a single
// element load/store).  The entry wrapper must therefore only take the
// native path when `ctx.gqr[0] == 0`.
inline void gx_load_pos_mtx_imm(GuestMem mem, uint64_t& inst_count, uint32_t mtx, uint32_t idx) {
  const uint32_t address = ((idx << 2) & 0xFFFFFFFCu) | 0xB0000u;
  mem.write8(gx::kBpPort, gx::kCpXfLoadTag);
  mem.write32(gx::kBpPort, address);
  for (uint32_t i = 0; i < 6u; ++i) {
    const uint32_t lo = psq_f32_bits(mem, mtx + 8u * i);
    const uint32_t hi = psq_f32_bits(mem, mtx + 8u * i + 4u);
    psq_store_pair(mem, gx::kBpPort, lo, hi);
  }
  inst_count += 20;  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXLoadNrmMtxImm  (func_803CDA9C, 46 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/ogws/revolution/GX/GXTransform.c :: GXLoadNrmMtxImm
//
// The guest loads the nine elements of the 3x3 normal matrix out of a 3x4
// padded matrix (the elements at offsets 12, 28 and 44 are skipped) and
// writes them into the XF with three PSQ pair stores and three `stfs`.
//
// The XF address is `(3 * idx + 0x400) | 0x80000`.
//
// The PSQ pair accesses need the guest's GQR0 to be the default (a single
// element load/store).  The entry wrapper must therefore only take the
// native path when `ctx.gqr[0] == 0`.
inline void gx_load_nrm_mtx_imm(GuestMem mem, uint64_t& inst_count, uint32_t mtx, uint32_t idx) {
  const uint32_t address = ((3u * idx + 0x400u) & 0xFFFFFFFFu) | 0x80000u;
  mem.write8(gx::kBpPort, gx::kCpXfLoadTag);
  mem.write32(gx::kBpPort, address);
  // The guest alternates psq_l/psq_st (a pair) and lfs/stfs (a single):
  //   mtx[0] mtx[1] | mtx[2] | mtx[4] mtx[5] | mtx[6] | mtx[8] mtx[9] | mtx[10]
  psq_store_pair(mem, gx::kBpPort, psq_f32_bits(mem, mtx + 0u),
                 psq_f32_bits(mem, mtx + 4u));
  mem.write_f32(gx::kBpPort, bits_f32(psq_f32_bits(mem, mtx + 8u)));
  psq_store_pair(mem, gx::kBpPort, psq_f32_bits(mem, mtx + 16u),
                 psq_f32_bits(mem, mtx + 20u));
  mem.write_f32(gx::kBpPort, bits_f32(psq_f32_bits(mem, mtx + 24u)));
  psq_store_pair(mem, gx::kBpPort, psq_f32_bits(mem, mtx + 32u),
                 psq_f32_bits(mem, mtx + 36u));
  mem.write_f32(gx::kBpPort, bits_f32(psq_f32_bits(mem, mtx + 40u)));
  inst_count += 22;  // the guest's retired instruction count
}

// ---------------------------------------------------------------------------
// GXLoadLightObjImm  (func_803CB590, 42 guest samples)
// ---------------------------------------------------------------------------
//
// Source: src/external/petari/RVL_SDK/gx/GXLight.c :: GXLoadLightObjImm
//
//   void GXLoadLightObjImm(const GXLightObj* lt_obj, GXLightID light)
//
// The guest converts the light id into a slot index (`31 - cntlzw`), masks
// it to three bits, sends the CP "load XF registers" command for
// 0x600 + index*0x10 with a length of 0xF and then writes the light object
// into the XF: three zero words, the colour and the twelve attribute floats.
//
// The light object layout (from the recovered `WriteLightObjPS` assembly and
// the translated `psq_l` displacements):
//
//   +0  .. +11   reserved (three zero words)
//   +12          colour (u32)
//   +16, +20     a[0], a[1]
//   +24, +28     a[2], k[0]
//   +32, +36     k[1], k[2]
//   +40, +44     lpos[0], lpos[1]
//   +48, +52     lpos[2], ldir[0]
//   +56, +60     ldir[1], ldir[2]
//
// The `cntlzw`/`subfic` pair updates the XER carry, so the carry is written
// back through the `xer` argument.
//
// The PSQ pair accesses need the guest's GQR0 to be the default (a single
// element load/store).  The entry wrapper must therefore only take the
// native path when `ctx.gqr[0] == 0` and the FPU is enabled (`MSR.FP`), since
// the translated body returns through the FP-unavailable exception otherwise.
//
// Registers written by the translated body (and reproduced here)
//
//     gpr0  1
//     gpr3  the GX state block pointer (sda-0x38E0)
//     gpr4  0xCC008000 (the `stwu r6, -0x8000(r4)` moves the 0xCC010000 base)
//     gpr5  ((31 - cntlzw(light)) & 7) << 4, the XF slot pair offset
//     gpr6  0 (the `xor r6, r6` before the three zero words)
//     fpr0..fpr5   the low element of each loaded pair, widened to double
//     ps1[0..5]    the high element of each loaded pair, widened to double
//     xer   the carry of `subfic r5, r0, 31`
//
//   The pair at light_obj+0x10+8*i is loaded into register 5-i (the guest
//   loads f5 first), so fpr[5-i] gets the first element and ps1[5-i] the
//   second.
struct LoadLightObjImmResult {
  uint32_t gpr0;
  uint32_t gpr3;
  uint32_t gpr4;
  uint32_t gpr5;
  uint32_t gpr6;
  double fpr[6];
  double ps1[6];
};

inline LoadLightObjImmResult gx_load_light_obj_imm(GuestMem mem, uint64_t& inst_count,
                                                   uint32_t sda, uint32_t light_obj,
                                                   uint32_t light, uint32_t& xer) {
  const uint32_t nlz = (light == 0u) ? 32u : static_cast<uint32_t>(__builtin_clz(light));
  // The guest's `subfic r5, r0, 31` computes 31 - nlz and sets the carry
  // when 31 >= nlz (i.e. when the light id is not zero).
  const uint32_t index = 31u - nlz;
  xer = (xer & ~(1u << 29)) | ((31u >= nlz) ? (1u << 29) : 0u);
  const uint32_t slot = index & 7u;

  mem.write8(gx::kBpPort, gx::kCpXfLoadTag);
  mem.write32(gx::kBpPort, ((slot << 4) + 0x600u) | 0xF0000u);

  const uint32_t color = mem.read32(light_obj + 12u);
  mem.write32(gx::kBpPort, 0u);
  mem.write32(gx::kBpPort, 0u);
  mem.write32(gx::kBpPort, 0u);
  mem.write32(gx::kBpPort, color);

  LoadLightObjImmResult r;
  for (uint32_t i = 0; i < 6u; ++i) {
    const uint32_t address = light_obj + 16u + 8u * i;
    const uint32_t lo = psq_f32_raw_bits(mem, address);
    const uint32_t hi = psq_f32_raw_bits(mem, address + 4u);
    psq_store_pair(mem, gx::kBpPort, psq_quiet_bits(lo), psq_quiet_bits(hi));
    // psq_load order: fr5 <- +0x10, fr4 <- +0x18, ... fr0 <- +0x38.
    r.fpr[5u - i] = bits_f32(lo);
    r.ps1[5u - i] = bits_f32(hi);
  }

  const uint32_t state = mem.read32(sda + static_cast<uint32_t>(gx::kStatePtrOffset));
  mem.write16(state + gx::kLastWriteWasXF, 1u);  // gx->bpSentNot = GX_TRUE
  inst_count += 31;  // the guest's retired instruction count

  r.gpr0 = 1u;               // 0x803CB5FC: li r0, 1
  r.gpr3 = state;            // 0x803CB600: lwz r3, -0x38E0(r2)
  r.gpr4 = gx::kBpPort;      // 0x803CB5D4: stwu r6, -0x8000(r4)
  r.gpr5 = slot << 4;        // 0x803CB5A0: rlwinm r5, r5, 4, 25, 27
  r.gpr6 = 0u;               // 0x803CB5B8: xor r6, r6
  return r;
}

// Publishes the whole clobbered set, including the paired-single halves the
// translated `psq_l` leaves behind.  fpr/ps1 outside 0..5 keep their entry
// value, so they are not touched.
inline void apply_gx_load_light_obj_imm(CPUContext& ctx) {
  const LoadLightObjImmResult r = gx_load_light_obj_imm(
      guest_mem(ctx), ctx.inst_count, ctx.gpr[2], ctx.gpr[3], ctx.gpr[4], ctx.xer);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.gpr[6] = r.gpr6;
  for (int i = 0; i < 6; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.ps1[i];
  }
  ctx.pc = ctx.lr;
}

}  // namespace nwii::runtime::native_lower
