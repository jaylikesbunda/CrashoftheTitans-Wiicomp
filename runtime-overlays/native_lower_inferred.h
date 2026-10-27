#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Inferred native lowerings: guest functions that have no recovered source in
// this repository and whose behaviour was reconstructed from the translated
// code and the PPC instructions the translator records on every line.
//
// These are NOT decompilations and are not claimed to be byte-exact
// decompilation of anything.  They are hand-written host kernels whose
// contract is fixed by the *translated body*: the oracle is
//
//   storage/crash-generated/*.cpp   NWII_GUEST_ABI void func_<addr>(...)
//
// and tools/native_lower_inferred_tests.cpp runs that body and the kernel on
// identical states and compares the ordered guest memory-effect log, the GX
// port log, the touched guest RAM, the whole register file (gpr, fpr, ps1,
// CR0-7, LR, CTR, XER, PC, MSR, FPSCR) and the retired guest instruction
// count.
//
// The rules the implementations follow are in native_lower_support.h: guest
// memory effects go through `GuestMem`, guest addresses are 32-bit and wrap,
// and no guest global is duplicated on the host.
#include "native_lower_support.h"

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// Condition-register field produced by `fcmpu` (and by `cror` on top of it).
//
// The translator's own expansion is reproduced verbatim: an unordered compare
// sets SO and clears LT/GT/EQ.  `cror` then ORs two of those bits; both the
// compare and the OR are visible in the translated body and are kept here so
// the published CR0 field is bit-identical.
// ---------------------------------------------------------------------------
struct Cr0Field {
  bool lt, gt, eq, so;
};

// The runtime stores every guest single-precision operand in a `double` field
// (CPUContext::fpr), so a value the guest keeps in an fpr is widened to double
// when it is loaded and narrowed again when it is stored.  That round trip is
// not the identity for a signalling NaN - the conversion sets the quiet bit -
// and a compiler is free to fold `(float)(double)x` back to `x`.  The
// translated body does not fold it (the value lives in memory between the two
// conversions), so the narrowing that carries a *loaded* value back to memory
// is kept observable; the result is bit-identical for every input, including
// the NaN payloads.
inline double widen_f32(float value) {
  // A plain widening conversion: the hardware conversion is exact for every
  // float, and for a signalling NaN it is the conversion itself that sets the
  // quiet bit, so nothing has to be arranged here.  (Only the narrowing needs
  // a barrier: see narrow_f32.)
  return static_cast<double>(value);
}

inline float narrow_f32(double value) {
  // `(float)(double)x` would be folded back to `x` by the compiler, which
  // loses the quiet bit exactly where the translated body sets it.  Only the
  // stores of a value that came straight from a float load need this; a value
  // produced by an arithmetic operation is narrowed with a plain cast.
  volatile double barrier = value;
  return static_cast<float>(barrier);
}

// The guest's fused multiply-add.
//
// The runtime spells `fmadds`/`ps_madds0`/`ps_madds1` as `std::fma(a, b, c)` on
// doubles, and the host fma returns its *first NaN operand* (quieted) when more
// than one operand is a NaN.  The compiler is allowed to commute the two
// multiplicands - they are commutative - which the translated body's object
// code does at some call sites and not at others, so the returned NaN payload
// would depend on the code generator.  The selection is therefore written out
// here, in the translated body's source order, and `std::fma` is only used when
// no operand is a NaN (where the operand order cannot matter: `inf * 0` and
// `inf - inf` invalid cases produce the same default quiet NaN either way).
// The kernel's result is then independent of the code generator; whether the
// *translated body* still is, is what the differential test measures (see the
// 0x8028199C note below).
inline double quiet_nan(double value) {
  uint64_t bits;
  std::memcpy(&bits, &value, 8);
  bits |= 0x0008000000000000ull;   // the double quiet bit
  double out;
  std::memcpy(&out, &bits, 8);
  return out;
}

inline double guest_fma(double a, double b, double c) {
  if (std::isnan(a)) return quiet_nan(a);
  if (std::isnan(b)) return quiet_nan(b);
  if (std::isnan(c)) return quiet_nan(c);
  return std::fma(a, b, c);
}

inline Cr0Field compare_unordered(double a, double b) {
  Cr0Field c;
  const bool un = std::isnan(a) || std::isnan(b);
  c.lt = !un && (a < b);
  c.gt = !un && (a > b);
  c.eq = !un && (a == b);
  c.so = un;
  return c;
}

// ---------------------------------------------------------------------------
// 0x802880E8 - 300 captured guest-thread timer samples (0.711 % of 42 211).
//
// Inferred from the translated body (29 interior entry points, no calls).
// The function is a leaf: it reads two float vectors, writes a 16-byte scratch
// area in its own stack frame, and returns a 0/1 flag.
//
// Recovered pseudocode (the guest's own register allocation is kept so that
// the ordered effect log and the instruction count are identical):
//
//     float f1 = a[0];
//     float f0 = *(float*)(SDA - 20280);      // r2 - 20280, a constant
//     sp -= 32; *(uint32_t*)sp = old_sp;      // stwu r1,-32(r1)
//     bool c1 = f1 >= f0;                     // fcmpu + cror 2,2,1
//     float f2 = c1 ? b[0] : b[3];
//     float f1b = a[1]; float f0b = *(SDA - 20280);
//     ((float*)sp)[2] = f2;
//     bool c2 = f1b >= f0b;
//     float f2b = c2 ? b[1] : b[4];
//     float f1c = a[2]; float f0c = *(SDA - 20280);
//     ((float*)sp)[3] = f2b;
//     bool c3 = f1c >= f0c;
//     float f6 = c3 ? b[2] : b[5];
//     float t3 = ((float*)sp)[3];
//     float t2 = (float)f6;                   // frsp
//     float m  = t3 * a[1];                   // fmuls
//     float t4 = ((float*)sp)[2];
//     float n  = fma(t4, a[0], m);            // fmadds
//     ((float*)sp)[4] = f6;
//     float r  = fma(t2, a[2], n);            // fmadds
//     float z  = -a[3];                       // fneg
//     return (r <= z);                        // fcmpu + cror 2,2,0 + mfcr
//                                             // + rlwinm r3,r3,3,31,31
//
// The arithmetic is *the translator's*, not the PPC's: `fpr` is a double in the
// runtime, so `fmuls`/`fmadds` are a double multiply / a double fma rounded
// once to float and widened again, and `fneg` negates the double (it does not
// update ps1).  Every operation below is written as the same expression on the
// same types, so the result is bit-identical for every input, including NaN,
// inf, denormal and signed zero.
//
// Behaviour contract
//   inputs   sp = r1, sda = r2, a = r3, b = r4
//   reads    read_f32(a + 0), read_f32(sda - 20280),                 (0xE8/0xEC)
//            read_f32(b + 0 | b + 12), read_f32(a + 4),
//            read_f32(sda - 20280),                                 (0x100..0x110)
//            read_f32(b + 4 | b + 16), read_f32(a + 8),
//            read_f32(sda - 20280),                                 (0x124..0x134)
//            read_f32(b + 8 | b + 20),                              (0x148 | 0x150)
//            read_f32(sp - 20), read_f32(a + 4), read_f32(a + 12),
//            read_f32(sp - 24), read_f32(a + 0), read_f32(a + 8)    (0x154..0x174)
//            The three SDA reads are separate accesses, in that order.
//   writes   write32(sp - 32, sp)            stwu r1,-32(r1)        (0x0F0)
//            write_f32(sp - 24, (float)f2)  stfs f2, 8(r1)          (0x114)
//            write_f32(sp - 20, (float)f2)  stfs f2,12(r1)          (0x138)
//            write_f32(sp - 16, (float)f6)  stfs f6,16(r1)          (0x17C)
//            (sp is the *entry* r1; all four addresses wrap in 32 bits.)
//   order    exactly as listed above; every access is a separate MMU call with
//            the same width as the translated path.
//   result   r3 = 1 when the last compare is LT or EQ (i.e. `r <= z`, false
//            when either operand is NaN), else 0.
//   guest registers written by the translated body
//            r3; f0..f6 and ps1 0..6 (every `lfs`/`frsp`/`fmuls`/`fmadds`
//            writes the paired-single mirror, `fneg` at 0x80288170 writes f0
//            only, so ps1[0] keeps the value of the `lfs f0,12(r3)`); CR0.
//            The native path publishes all of them.
//            r1 is restored by `addi r1,r1,32` and keeps its entry value.
//   inst_count
//            the translated body retires 36 + (c1 ? 1 : 0) + (c2 ? 1 : 0) +
//            (c3 ? 1 : 0) instructions, i.e. 36..39.  That is not the count of
//            retired PPC instructions: the translator does not count the three
//            `b` instructions at 0x80288104 / 0x80288128 / 0x8028814C, so the
//            real PPC count is exactly 3 higher (39..42) on every path.  The
//            native path reproduces the *translated* count, because that is the
//            value the runtime's virtual clock (read_timebase) consumed.
//   fallback the MSR.FP guard keeps the translated path whenever the guest has
//            the FPU disabled (the translated body takes its `require_fpu`
//            early return there and this kernel does not model the exception
//            frame).  Interior entries, traced calls and the kill switch are
//            handled by the staged entry.
// ---------------------------------------------------------------------------
struct Result802880e8 {
  uint32_t gpr3;
  double fpr[7];   // f0..f6
  double ps1[7];   // ps1 0..6
  Cr0Field cr0;
};

inline Result802880e8 inferred_802880e8(GuestMem mem, uint64_t& inst_count,
                                        uint32_t sp, uint32_t sda,
                                        uint32_t a, uint32_t b) {
  Result802880e8 r;
  double f0, f1, f2, f3, f4, f5, f6;
  double p0, p1, p2, p3, p4, p5, p6;

  // The frame is addressed with the entry r1; the `stwu` pre-decrements.
  const uint32_t frame = sp - 32u;

  f1 = p1 = widen_f32(mem.read_f32(a + 0u));        // 0x802880E8 lfs f1,0(r3)
  f0 = p0 = widen_f32(mem.read_f32(sda - 20280u));  // 0x802880EC lfs f0,-20280(r2)
  mem.write32(frame, sp);                                     // 0x802880F0 stwu r1,-32(r1)

  // 0x802880E8 (entry count), the two lfs, the stwu and the fcmpu/cror pair.
  uint64_t retired = 6;

  Cr0Field c = compare_unordered(f1, f0);                     // 0x802880F4 fcmpu
  // 0x802880F8 cror 2,2,1; 0x802880FC bne.  The two arms load from different
  // addresses, so the single access below is at the address the taken arm
  // would use; the ordered effect log is unchanged.
  const bool c1 = c.gt | c.eq;
  retired += c1 ? 2 : 1;
  f2 = p2 = widen_f32(mem.read_f32(b + (c1 ? 0u : 12u)));     // lfs f2,0|12(r4)

  f1 = p1 = widen_f32(mem.read_f32(a + 4u));        // 0x8028810C lfs f1,4(r3)
  f0 = p0 = widen_f32(mem.read_f32(sda - 20280u));  // 0x80288110 lfs f0,-20280(r2)
  mem.write_f32(frame + 8u, narrow_f32(f2));          // 0x80288114 stfs f2,8(r1)
  retired += 5;                                               // 0x8028810C..0x80288118

  c = compare_unordered(f1, f0);                              // 0x80288118 fcmpu
  const bool c2 = c.gt | c.eq;                                // 0x8028811C cror 2,2,1
  retired += c2 ? 2 : 1;                                      // 0x80288120 bne
  f2 = p2 = widen_f32(mem.read_f32(b + (c2 ? 4u : 16u)));     // lfs f2,4|16(r4)

  f1 = p1 = widen_f32(mem.read_f32(a + 8u));        // 0x80288130 lfs f1,8(r3)
  f0 = p0 = widen_f32(mem.read_f32(sda - 20280u));  // 0x80288134 lfs f0,-20280(r2)
  mem.write_f32(frame + 12u, narrow_f32(f2));         // 0x80288138 stfs f2,12(r1)
  retired += 5;                                               // 0x80288130..0x8028813C

  c = compare_unordered(f1, f0);                              // 0x8028813C fcmpu
  const bool c3 = c.gt | c.eq;                                // 0x80288140 cror 2,2,1
  retired += c3 ? 2 : 1;                                      // 0x80288144 bne
  f6 = p6 = widen_f32(mem.read_f32(b + (c3 ? 8u : 20u)));     // lfs f6,8|20(r4)

  f3 = p3 = widen_f32(mem.read_f32(frame + 12u));   // 0x80288154 lfs f3,12(r1)
  f2 = p2 = static_cast<double>(static_cast<float>(f6));  // 0x80288158 frsp f2,f6
  f1 = p1 = widen_f32(mem.read_f32(a + 4u));        // 0x8028815C lfs f1,4(r3)
  f0 = p0 = widen_f32(mem.read_f32(a + 12u));       // 0x80288160 lfs f0,12(r3)
  f5 = p5 = static_cast<float>(f3 * f1);              // 0x80288164 fmuls f5,f3,f1
  f4 = p4 = widen_f32(mem.read_f32(frame + 8u));    // 0x80288168 lfs f4,8(r1)
  f3 = p3 = widen_f32(mem.read_f32(a + 0u));        // 0x8028816C lfs f3,0(r3)
  f0 = -f0;                                                   // 0x80288170 fneg f0,f0
  f1 = p1 = widen_f32(mem.read_f32(a + 8u));        // 0x80288174 lfs f1,8(r3)
  // The two `fmadds`, with the operands in the translated body's order
  // (`std::fma(ctx.fpr[4], ctx.fpr[3], ctx.fpr[5])` and
  // `std::fma(ctx.fpr[2], ctx.fpr[1], ctx.fpr[3])`).
  f3 = p3 = static_cast<float>(guest_fma(f4, f3, f5));  // 0x80288178 fmadds f3,f4,f3,f5
  mem.write_f32(frame + 16u, narrow_f32(f6));         // 0x8028817C stfs f6,16(r1)
  f1 = p1 = static_cast<float>(guest_fma(f2, f1, f3));  // 0x80288180 fmadds f1,f2,f1,f3
  retired += 17;                                              // 0x80288154..0x80288184

  c = compare_unordered(f1, f0);                              // 0x80288184 fcmpu
  const bool cr_eq = c.lt | c.eq;                             // 0x80288188 cror 2,2,0
  // 0x8028818C mfcr r3; 0x80288190 rlwinm r3,r3,3,31,31.  The translator
  // builds the CR word with LT at bit 31, GT at 30, EQ at 29 and SO at 28, and
  // `rotl(word, 3) & 1` selects bit 29, i.e. CR0.EQ.
  r.gpr3 = cr_eq ? 1u : 0u;
  r.cr0.lt = c.lt;
  r.cr0.gt = c.gt;
  r.cr0.eq = cr_eq;
  r.cr0.so = c.so;
  r.fpr[0] = f0; r.fpr[1] = f1; r.fpr[2] = f2; r.fpr[3] = f3;
  r.fpr[4] = f4; r.fpr[5] = f5; r.fpr[6] = f6;
  r.ps1[0] = p0; r.ps1[1] = p1; r.ps1[2] = p2; r.ps1[3] = p3;
  r.ps1[4] = p4; r.ps1[5] = p5; r.ps1[6] = p6;
  inst_count += retired;
  return r;
}

inline void apply_802880e8(CPUContext& ctx) {
  const Result802880e8 r = inferred_802880e8(guest_mem(ctx), ctx.inst_count,
                                             ctx.gpr[1], ctx.gpr[2],
                                             ctx.gpr[3], ctx.gpr[4]);
  ctx.gpr[3] = r.gpr3;
  for (int i = 0; i < 7; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.ps1[i];
  }
  ctx.cr[0].lt = r.cr0.lt;
  ctx.cr[0].gt = r.cr0.gt;
  ctx.cr[0].eq = r.cr0.eq;
  ctx.cr[0].so = r.cr0.so;
  ctx.pc = ctx.lr;
}

// ---------------------------------------------------------------------------
// 0x8028199C - 171 captured guest-thread timer samples (0.405 %), 77 direct
// call sites, 17 paired-single operations, no calls.
//
// ADOPTION STATUS: recovered and implemented, but NOT recommended for the
// manifest.  The kernel is exact in the configuration the differential test
// runs in (377/377 cases: ordered effect log, touched RAM, whole register file,
// retired count), but in the production configuration one case of 377 differs -
// a single NaN payload bit of one stored float.  The cause is the *oracle*: the
// translated body's `std::fma`-based ps_madds* chain returns whichever NaN the
// host fma selects, and that selection follows the multiplicand order the
// compiler happens to emit, which changes between the recording build (every
// access is an out-of-line recorder call) and the production build.  No single
// `definition` entry can be bit-exact in both, and the production build is the
// one that ships, so this entry is left out of the manifest.  Everything below
// records what was recovered.
//
// Inferred from the translated body.  The function transforms four points by a
// 3x4 affine matrix and stores a 4x4 float block:
//
//     void f(Vec4* dst, const Point4* p, const Basis* b)   // r3, r4, r5
//     {
//         float zero = f11 - f11;          // fsub f11,f11,f11; r6 = 32 is dead
//         for (i = 0; i < 4; ++i) {
//             dst[i].x = b[0]*p[i].x + b[4]*p[i].y + b[8] *p[i].z + b[12];
//             dst[i].y = b[1]*p[i].x + b[5]*p[i].y + b[9] *p[i].z + b[13];
//             dst[i].z = b[2]*p[i].x + b[6]*p[i].y + b[10]*p[i].z;
//             dst[i].w = (i == 3) ? 1.0f : zero;
//         }
//     }
//
// (b[] is 16 floats at r5, p[] is 16 floats at r4, dst[] is 16 floats at r3; of
// each group of four only the first three are read, and the fourth component
// written is zero except for the last group, where `ps_merge01` puts the 1.0
// produced by the single-element `psq_l` at 0x802819E8 into the w lane.)
//
// The exact operand order is the translated body's: the first product of each
// row is formed by `ps_muls0`, then `ps_madds1` and `ps_madds0` accumulate the
// second and third terms, and the two translation terms are added by `ps_add`
// at the end.
//
// The paired-single state is not modelled here: `psq_l`/`psq_st` go through the
// runtime's own CPUContext implementation, so the GQR conversion
// (GQR0 load/store type and scale), the watch diversion, the single-element
// store width and the eight-byte paired store are byte-identical to the
// translated path for *every* GQR0 value.  No GQR guard is needed.  The ps_*
// arithmetic is reproduced with the same double expressions the runtime's
// inline ps_* helpers use (`ps_muls0` is a plain double multiply, `ps_madds0/1`
// a double `std::fma`, `ps_add` a double add, `ps_merge01` a copy), so no
// intermediate is rounded to float where the runtime does not round.
//
// Behaviour contract
//   inputs   dest = r3, points = r4, basis = r5, f11 = ctx.fpr[11]
//            plus the whole psq/GQR state the two psq_l/psq_st streams consume
//   reads    psq_l(points + 0, W=0), read_f32(points + 8),
//            psq_l(basis + 0, W=0), read_f32(basis + 8),
//            psq_l(basis + 16, W=0), read_f32(basis + 24),
//            psq_l(basis + 32, W=0), read_f32(basis + 40),
//            psq_l(basis + 48, W=0), psq_l(basis + 56, W=1),
//            psq_l(points + 16, W=0), read_f32(points + 24),
//            psq_l(points + 32, W=0), read_f32(points + 40),
//            psq_l(points + 48, W=0), read_f32(points + 56)
//            interleaved with the stores exactly as in the translated body.
//   writes   psq_st(dest + 0, W=0), psq_st(dest + 8, W=1),
//            write_f32(dest + 12), psq_st(dest + 16, W=0), psq_st(dest + 24, W=1),
//            write_f32(dest + 28), psq_st(dest + 32, W=0), psq_st(dest + 40, W=1),
//            write_f32(dest + 44), psq_st(dest + 48, W=0), psq_st(dest + 56, W=0)
//            (64 bytes, in this order, every access a separate call).
//   result   none (the guest function returns void)
//   guest registers written by the translated body
//            f0..f9, f11, f12, f13 and their ps1 halves (f11 through `fsub`,
//            the rest through `psq_l`/`lfs`/`ps_*`), and r6 = 32.
//            ps1[11] is not written.  The native path publishes all of them.
//   inst_count
//            62 on every path.  This matches the real PPC count of the 62
//            instructions from 0x8028199C to 0x80281A90 inclusive (the two
//            `isync` instructions at 0x8028199C/0x802819A4 and the `blr` are
//            not counted by the translator, and the entry counter plus the
//            `+= 2` at 0x802819A0 make up the difference).
//   fallback the MSR.FP guard keeps the translated path when the guest has the
//            FPU disabled; interior entries, traced calls and the kill switch
//            are handled by the staged entry.
// ---------------------------------------------------------------------------
struct Result8028199c {
  double fpr[14];   // f0..f13 (f10 is never written by the guest)
  double ps1[14];
  uint32_t gpr6;
};

// The paired-single helpers, spelled out with the runtime's own expressions.
// `ctx.ps_muls0(D,A,C)` in cpu_context.h is exactly
//     fpr[D] = fpr[A] * fpr[C]; ps1[D] = ps1[A] * fpr[C];
// `ps_madds0/1` are the same two statements with `std::fma`, which the kernel
// spells through guest_fma so the NaN it returns does not depend on the code
// generator; `ps_add` is a plain double add.  These are the same statements on
// local values, in the same order.
inline Result8028199c inferred_8028199c(GuestMem mem, uint64_t& inst_count,
                                        CPUContext& ctx, double f11) {
  Result8028199c r;
  double f0, p0, f1, p1, f2, p2, f3, p3, f4, p4, f5, p5;
  double f6, p6, f7, p7, f8, p8, f9, p9, f12, p12, f13, p13;

  const uint32_t dest = ctx.gpr[3];
  const uint32_t points = ctx.gpr[4];
  const uint32_t basis = ctx.gpr[5];

  f0 = p0 = f1 = p1 = f2 = p2 = f3 = p3 = f4 = p4 = f5 = p5 = 0.0;
  f6 = p6 = f7 = p7 = f8 = p8 = f9 = p9 = f12 = p12 = f13 = p13 = 0.0;

  // 0x8028199C isync ; 0x802819A0 fsub f11,f11,f11 ; 0x802819A4 isync
  const double f11r = f11 - f11;
  const uint32_t gpr6 = 32u;                                        // 0x802819A8 li r6,32

  ctx.psq_load(0u, points + 0u, 0u, 0u);                            // 0x802819AC psq_l
  f0 = ctx.fpr[0]; p0 = ctx.ps1[0];
  f1 = p1 = widen_f32(mem.read_f32(points + 8u));         // 0x802819B0 lfs f1,8(r4)
  ctx.psq_load(2u, basis + 0u, 0u, 0u);                             // 0x802819B4 psq_l
  f2 = ctx.fpr[2]; p2 = ctx.ps1[2];
  f3 = p3 = widen_f32(mem.read_f32(basis + 8u));          // 0x802819B8 lfs f3,8(r5)
  ctx.psq_load(4u, basis + 16u, 0u, 0u);                            // 0x802819BC psq_l
  f4 = ctx.fpr[4]; p4 = ctx.ps1[4];
  f5 = p5 = widen_f32(mem.read_f32(basis + 24u));         // 0x802819C0 lfs f5,24(r5)
  // 0x802819C4 isync

  f12 = f2 * f0;                                                    // 0x802819C8 ps_muls0 12,2,0
  p12 = p2 * f0;
  ctx.psq_load(6u, basis + 32u, 0u, 0u);                            // 0x802819CC psq_l
  f6 = ctx.fpr[6]; p6 = ctx.ps1[6];
  f13 = f3 * f0;                                                    // 0x802819D0 ps_muls0 13,3,0
  p13 = p3 * f0;
  f7 = p7 = widen_f32(mem.read_f32(basis + 40u));         // 0x802819D4 lfs f7,40(r5)
  // 0x802819D8 isync

  f12 = guest_fma(f4, p0, f12);                                      // 0x802819DC ps_madds1 12,4,0,12
  p12 = guest_fma(p4, p0, p12);
  ctx.psq_load(8u, basis + 48u, 0u, 0u);                            // 0x802819E0 psq_l
  f8 = ctx.fpr[8]; p8 = ctx.ps1[8];
  f13 = guest_fma(f5, p0, f13);                                      // 0x802819E4 ps_madds1 13,5,0,13
  p13 = guest_fma(p5, p0, p13);
  ctx.psq_load(9u, basis + 56u, 1u, 0u);                            // 0x802819E8 psq_l (W=1)
  f9 = ctx.fpr[9]; p9 = ctx.ps1[9];

  f12 = guest_fma(f6, f1, f12);                                      // 0x802819EC ps_madds0 12,6,1,12
  p12 = guest_fma(p6, f1, p12);
  ctx.psq_load(0u, points + 16u, 0u, 0u);                           // 0x802819F0 psq_l
  f0 = ctx.fpr[0]; p0 = ctx.ps1[0];
  f13 = guest_fma(f7, f1, f13);                                      // 0x802819F4 ps_madds0 13,7,1,13
  p13 = guest_fma(p7, f1, p13);
  f1 = p1 = widen_f32(mem.read_f32(points + 24u));        // 0x802819F8 lfs f1,24(r4)

  ctx.fpr[12] = f12; ctx.ps1[12] = p12;                             // psq_st source
  ctx.psq_store(12u, dest + 0u, 0u, 0u);                            // 0x802819FC psq_st
  f12 = f2 * f0;                                                    // 0x80281A00 ps_muls0 12,2,0
  p12 = p2 * f0;
  ctx.fpr[13] = f13; ctx.ps1[13] = p13;
  ctx.psq_store(13u, dest + 8u, 1u, 0u);                            // 0x80281A04 psq_st (W=1)
  f13 = f3 * f0;                                                    // 0x80281A08 ps_muls0 13,3,0
  p13 = p3 * f0;
  mem.write_f32(dest + 12u, narrow_f32(f11r));              // 0x80281A0C stfs f11,12(r3)

  f12 = guest_fma(f4, p0, f12);                                      // 0x80281A10 ps_madds1 12,4,0,12
  p12 = guest_fma(p4, p0, p12);
  // 0x80281A14 isync
  f13 = guest_fma(f5, p0, f13);                                      // 0x80281A18 ps_madds1 13,5,0,13
  p13 = guest_fma(p5, p0, p13);
  ctx.psq_load(0u, points + 32u, 0u, 0u);                           // 0x80281A1C psq_l
  f0 = ctx.fpr[0]; p0 = ctx.ps1[0];
  f12 = guest_fma(f6, f1, f12);                                      // 0x80281A20 ps_madds0 12,6,1,12
  p12 = guest_fma(p6, f1, p12);
  f13 = guest_fma(f7, f1, f13);                                      // 0x80281A24 ps_madds0 13,7,1,13
  p13 = guest_fma(p7, f1, p13);
  f1 = p1 = widen_f32(mem.read_f32(points + 40u));        // 0x80281A28 lfs f1,40(r4)

  ctx.fpr[12] = f12; ctx.ps1[12] = p12;
  ctx.psq_store(12u, dest + 16u, 0u, 0u);                           // 0x80281A2C psq_st
  f12 = f2 * f0;                                                    // 0x80281A30 ps_muls0 12,2,0
  p12 = p2 * f0;
  ctx.fpr[13] = f13; ctx.ps1[13] = p13;
  ctx.psq_store(13u, dest + 24u, 1u, 0u);                           // 0x80281A34 psq_st (W=1)
  f13 = f3 * f0;                                                    // 0x80281A38 ps_muls0 13,3,0
  p13 = p3 * f0;
  mem.write_f32(dest + 28u, narrow_f32(f11r));              // 0x80281A3C stfs f11,28(r3)

  f12 = guest_fma(f4, p0, f12);                                      // 0x80281A40 ps_madds1 12,4,0,12
  p12 = guest_fma(p4, p0, p12);
  f13 = guest_fma(f5, p0, f13);                                      // 0x80281A44 ps_madds1 13,5,0,13
  p13 = guest_fma(p5, p0, p13);
  ctx.psq_load(0u, points + 48u, 0u, 0u);                           // 0x80281A48 psq_l
  f0 = ctx.fpr[0]; p0 = ctx.ps1[0];
  f12 = guest_fma(f6, f1, f12);                                      // 0x80281A4C ps_madds0 12,6,1,12
  p12 = guest_fma(p6, f1, p12);
  f13 = guest_fma(f7, f1, f13);                                      // 0x80281A50 ps_madds0 13,7,1,13
  p13 = guest_fma(p7, f1, p13);
  f1 = p1 = widen_f32(mem.read_f32(points + 56u));        // 0x80281A54 lfs f1,56(r4)

  ctx.fpr[12] = f12; ctx.ps1[12] = p12;
  ctx.psq_store(12u, dest + 32u, 0u, 0u);                           // 0x80281A58 psq_st
  f12 = f2 * f0;                                                    // 0x80281A5C ps_muls0 12,2,0
  p12 = p2 * f0;
  ctx.fpr[13] = f13; ctx.ps1[13] = p13;
  ctx.psq_store(13u, dest + 40u, 1u, 0u);                           // 0x80281A60 psq_st (W=1)
  f13 = f3 * f0;                                                    // 0x80281A64 ps_muls0 13,3,0
  p13 = p3 * f0;

  f12 = guest_fma(f4, p0, f12);                                      // 0x80281A68 ps_madds1 12,4,0,12
  p12 = guest_fma(p4, p0, p12);
  f13 = guest_fma(f5, p0, f13);                                      // 0x80281A6C ps_madds1 13,5,0,13
  p13 = guest_fma(p5, p0, p13);
  mem.write_f32(dest + 44u, narrow_f32(f11r));              // 0x80281A70 stfs f11,44(r3)
  f12 = guest_fma(f6, f1, f12);                                      // 0x80281A74 ps_madds0 12,6,1,12
  p12 = guest_fma(p6, f1, p12);
  f13 = guest_fma(f7, f1, f13);                                      // 0x80281A78 ps_madds0 13,7,1,13
  p13 = guest_fma(p7, f1, p13);

  f12 = f12 + f8;                                                   // 0x80281A7C ps_add 12,12,8
  p12 = p12 + p8;
  f13 = f13 + f9;                                                   // 0x80281A80 ps_add 13,13,9
  p13 = p13 + p9;
  // 0x80281A84 ps_merge01 13,13,9: fpr[13] keeps its value, ps1[13] = ps1[9].
  p13 = p9;

  ctx.fpr[12] = f12; ctx.ps1[12] = p12;
  ctx.psq_store(12u, dest + 48u, 0u, 0u);                           // 0x80281A88 psq_st
  ctx.fpr[13] = f13; ctx.ps1[13] = p13;
  ctx.psq_store(13u, dest + 56u, 0u, 0u);                           // 0x80281A8C psq_st
  // 0x80281A90 blr

  inst_count += 62;
  r.fpr[0] = f0; r.fpr[1] = f1; r.fpr[2] = f2; r.fpr[3] = f3;
  r.fpr[4] = f4; r.fpr[5] = f5; r.fpr[6] = f6; r.fpr[7] = f7;
  r.fpr[8] = f8; r.fpr[9] = f9; r.fpr[11] = f11r;
  r.fpr[12] = f12; r.fpr[13] = f13;
  r.ps1[0] = p0; r.ps1[1] = p1; r.ps1[2] = p2; r.ps1[3] = p3;
  r.ps1[4] = p4; r.ps1[5] = p5; r.ps1[6] = p6; r.ps1[7] = p7;
  r.ps1[8] = p8; r.ps1[9] = p9;
  r.ps1[12] = p12; r.ps1[13] = p13;
  r.gpr6 = gpr6;
  return r;
}

inline void apply_8028199c(CPUContext& ctx) {
  const Result8028199c r = inferred_8028199c(guest_mem(ctx), ctx.inst_count,
                                             ctx, ctx.fpr[11]);
  for (int i = 0; i <= 9; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.ps1[i];
  }
  ctx.fpr[11] = r.fpr[11];   // the `fsub` writes f11, not the paired-single half
  for (int i = 12; i <= 13; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.ps1[i];
  }
  ctx.gpr[6] = r.gpr6;
  ctx.pc = ctx.lr;
}

}  // namespace nwii::runtime::native_lower
