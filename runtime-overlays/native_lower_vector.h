#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Source-backed native lowering of the guest's vector / matrix / floating point
// computations and of the ordinary RAM leaves that surround them.
//
// Every function in this header replaces the *translated* body of one guest
// function with a host implementation written from that function's recovered
// C/C++ source.  The rules every lowering follows are the ones of
// native_lower_support.h:
//
//   * Guest memory effects go through GuestMem, so the runtime MMU keeps
//     watchpoints, MMIO, FIFO writes, command ordering and publication
//     identical to the translated path.
//   * Guest addresses are 32 bit and wrap: every address computation is done
//     in uint32_t.
//   * No host copy of a guest global: the guest's own small-data-area base
//     (r2 = SDA, r13 = SDA2) is passed in as an argument and every global is
//     read from - and written to - guest memory.
//   * Guest layout (struct offsets, bitfields) is taken from the translated
//     guest code, never from a host sizeof/offsetof.
//   * Integer and FP semantics match the translated body exactly, including
//     the order of the operations, the single-precision rounding of every
//     result and the fused multiply-add.
//
// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------
//
// Every kernel has the fixed shape of native_lower_support.h:
//
//   Result impl(GuestMem mem, uint64_t& inst_count, <typed arguments>);
//
//   * the guest's argument registers arrive as typed parameters;
//   * every guest memory effect goes through `mem`;
//   * `inst_count` receives the number of guest instructions the native path
//     replaces, so the runtime's instruction counter (which is also the guest
//     visible timebase) keeps the value the translated path produced;
//   * the returned value is a struct of the guest registers the native path
//     writes back.
//
// The `apply_*` helpers at the end of this header publish that struct into the
// guest register file.  The staged entry (tools/native_lowering.py) calls one
// of them, so the staged code is
//
//   NWII_GUEST_ABI void func_8028A914(NWII_CTX_PARAM ctx) {
//     if (ctx.pc == 0x8028A914) { native_lower::apply_vector_x_transform(ctx); ctx.pc = ctx.lr; return; }
//     native_original_8028A914(ctx);
//   }
//
// The translated body is never modified: it is renamed to
// `native_original_<address>` and stays reachable for every entry the native
// path does not cover (interior entry, traced call, disabled lowering, a
// candidate guard that does not hold).
//
// ---------------------------------------------------------------------------
// The single-precision arithmetic
// ---------------------------------------------------------------------------
//
// The guest's vector code is compiled with `fp_contract on`, so the guest's
// matrix transform uses fused multiply-adds (fmadds).  The translated body
// renders them as
//
//   ctx.fpr[5] = ctx.ps1[5] = (float)std::fma(fpr4, fpr7, fpr5);
//
// i.e. the operands live in double registers, the fused operation is done in
// double and the result is rounded to single precision once.  The helpers
// below reproduce exactly that, so the native path is bit identical to the
// translated path (which is the oracle), and not to the original Wii
// hardware's single-precision fmadd.
//
// The order of the operations matters as well: the guest evaluates
//
//   x = m[0][0]*src.x + m[1][0]*src.y + m[2][0]*src.z + m[3][0]
//
// as ((m00*src.x + m10*src.y) + m20*src.z) + m30 with the first two products
// fused into the adds, so the native path performs the same three operations
// in the same order.

#if __has_include("native_lower_support.h")
#include "native_lower_support.h"
#else
#include "../runtime-overlays/native_lower_support.h"
#endif

#include <cmath>
#include <cstdint>
#include <cstring>

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// The guest's single-precision operations, exactly as the translated body
// performs them.
// ---------------------------------------------------------------------------

// fmuls: the product of two single-precision values, rounded once.
inline float guest_fmuls(float a, float b) {
  return static_cast<float>(static_cast<double>(a) * static_cast<double>(b));
}

// fadds / fsubs: the sum or difference of two single-precision values.
inline float guest_fadds(float a, float b) {
  return static_cast<float>(static_cast<double>(a) + static_cast<double>(b));
}
inline float guest_fsubs(float a, float b) {
  return static_cast<float>(static_cast<double>(a) - static_cast<double>(b));
}

// fmadds: the fused multiply-add of the translated body.  The operands are
// held in double registers, so the fused operation is evaluated in double and
// the result is rounded to single precision once - exactly what
// `(float)std::fma(a, b, c)` computes in the translated body.
inline float guest_fmadds(float a, float b, float c) {
  return static_cast<float>(std::fma(static_cast<double>(a), static_cast<double>(b),
                                     static_cast<double>(c)));
}

// ---------------------------------------------------------------------------
// 0x80219D40 - FUN_80219d40 (src/master_match/c_80219d40.c)
//
//   int FUN_80219d40(register int param_1,register int param_2)
//   {
//     return *(int *)(param_1 + param_2 * 4 + 0x1c);
//   }
//
// The largest static call-site count of any source-backed candidate in the
// capture (1203 sites); the leaf itself is four guest instructions.
//
// Behaviour contract
//   inputs  : base = r3, index = r4
//   reads   : read32(base + (index << 2) + 0x1C); the index is scaled with the
//             guest's `rlwinm r0, r4, 2, 0, 29`, so the address wraps in 32 bit
//   writes  : none
//   result  : the loaded word (the guest's `lwz` zero-extends it)
//   registers written by the translated body: gpr0 (the scaled index) and
//             gpr3 (the loaded word).  The native path reproduces both.
//   guest instructions: 4
// ---------------------------------------------------------------------------
struct IndexedIntLoadResult {
  uint32_t value;   // r3
  uint32_t gpr0;    // the scaled index the guest leaves in r0
};

inline IndexedIntLoadResult indexed_int_load(GuestMem mem, uint64_t& inst_count,
                                             uint32_t base, uint32_t index) {
  // rlwinm r0, r4, 2, 0, 29 : index * 4 (mod 2^32)
  const uint32_t scaled = (index << 2) & 0xFFFFFFFCu;
  // add r3, r3, r0 ; lwz r3, 0x1C(r3)
  const uint32_t address = base + scaled + 28u;
  inst_count += 4;   // rlwinm, add, lwz, blr
  IndexedIntLoadResult r;
  r.value = mem.read32(address);
  r.gpr0 = scaled;
  return r;
}

// ---------------------------------------------------------------------------
// 0x8026B00C - FUN_8026b00c (src/master_match/c_8026b00c.c)
//
//   #pragma sdata2
//   extern int sda2_805972a8;
//   int FUN_8026b00c(register int param_1)
//   {
//     int uVar1;
//     uVar1 = *(int *)&sda2_805972a8;
//     *(int *)&sda2_805972a8  = (int)param_1;
//     return uVar1;
//   }
//
// The guest reaches the global with `lwz/stw -11832(r13)`, so the kernel takes
// the guest's own small-data-area-2 base and the global is read from and
// written to guest memory (there is no host copy of it).
//
// Behaviour contract
//   inputs  : sda2 = r13, value = r3
//   reads   : read32(sda2 - 11832)
//   writes  : write32(sda2 - 11832, value) - after the read
//   result  : the old value of the global
//   registers written by the translated body: gpr0 (the new value) and gpr3
//             (the old value).  The native path reproduces both.
//   guest instructions: 4
// ---------------------------------------------------------------------------
struct Sda2GlobalSwapResult {
  uint32_t value;   // r3: the old value of the global
  uint32_t gpr0;    // r0: the value the guest moved into r0
};

inline Sda2GlobalSwapResult sda2_global_swap(GuestMem mem, uint64_t& inst_count,
                                              uint32_t sda2, uint32_t value) {
  // mr r0, r3 ; lwz r3, -11832(r13) ; stw r0, -11832(r13) ; blr
  const uint32_t old = mem.read32(sda2 - 11832u);
  mem.write32(sda2 - 11832u, value);
  inst_count += 4;
  Sda2GlobalSwapResult r;
  r.value = old;
  r.gpr0 = value;
  return r;
}

// ---------------------------------------------------------------------------
// 0x8028669C - FillTranslate_ (src/master_match/c_8028669c.c)
//
//   void FillTranslate_(float *m, const float *v)
//   {
//     m[12] = v[0];
//     m[13] = v[1];
//     m[14] = v[2];
//   }
//
// The guest's `lfs`/`stfs` update the paired-single mirror of the FPR as
// well, so the native path publishes f0..f2 and ps1 0..2.
//
// Behaviour contract
//   inputs  : matrix = r3, vector = r4
//   reads   : read_f32(vector + 0), read_f32(vector + 4), read_f32(vector + 8)
//   writes  : write_f32(matrix + 48), write_f32(matrix + 52),
//             write_f32(matrix + 56) - after the three reads
//   result  : none (the guest function returns void)
//   registers written by the translated body: fpr0, fpr1, fpr2, ps1 0..2.
//             The native path reproduces all of them.
//   guest instructions: 7
// ---------------------------------------------------------------------------
struct FillTranslateResult {
  double fpr[3];   // f0..f2
};

inline FillTranslateResult fill_translate(GuestMem mem, uint64_t& inst_count,
                                          uint32_t matrix, uint32_t vector) {
  // lfs f2, 0(r4) ; lfs f1, 4(r4) ; lfs f0, 8(r4)
  const float v2 = mem.read_f32(vector + 0u);
  const float v1 = mem.read_f32(vector + 4u);
  const float v0 = mem.read_f32(vector + 8u);
  // stfs f2, 48(r3) ; stfs f1, 52(r3) ; stfs f0, 56(r3)
  mem.write_f32(matrix + 48u, v2);
  mem.write_f32(matrix + 52u, v1);
  mem.write_f32(matrix + 56u, v0);
  inst_count += 7;   // 3 lfs, 3 stfs, blr
  FillTranslateResult r;
  r.fpr[0] = static_cast<double>(v0);
  r.fpr[1] = static_cast<double>(v1);
  r.fpr[2] = static_cast<double>(v2);
  return r;
}

// ---------------------------------------------------------------------------
// 0x80286D08 - RadicalMathLibrary::Matrix::Transform
//               (src/master_match/c_80286d08.cpp)
//
//   void Matrix::Transform(const Vector& src, Vector* dest) const
//   {
//     float x,y,z;
//     x = m[0][0]*src.x + m[1][0]*src.y + m[2][0]*src.z + m[3][0];
//     y = m[0][1]*src.x + m[1][1]*src.y + m[2][1]*src.z + m[3][1];
//     z = m[0][2]*src.x + m[1][2]*src.y + m[2][2]*src.z + m[3][2];
//     dest->Set(x,y,z);
//   }
//
// The matrix is `float m[4][4]`, so m[i][j] is at matrix + i*16 + j*4.
// The guest only uses the first three columns plus the translation row.
//
// The kernel below reproduces the translated body operation by operation, in
// the translated body's order, so the result is bit identical:
//
//   fpr3 = m[1][0]      fpr2 = src.y     fpr1 = m[1][1]   fpr5 = fpr3*fpr2
//   fpr0 = m[1][2]      fpr3 = fpr1*fpr2 fpr4 = m[0][0]   fpr1 = fpr0*fpr2
//   fpr7 = src.x        fpr2 = m[0][1]   fpr5 = fmadds(fpr4, fpr7, fpr5)
//   fpr0 = m[0][2]      fpr3 = fmadds(fpr2, fpr7, fpr3)
//   fpr4 = m[2][0]      fpr1 = fmadds(fpr0, fpr7, fpr1)
//   fpr6 = src.z        fpr2 = m[2][1]   fpr0 = m[2][2]
//   fpr5 = fmadds(fpr4, fpr6, fpr5)     fpr4 = m[3][0]
//   fpr3 = fmadds(fpr2, fpr6, fpr3)     fpr2 = m[3][1]
//   fpr1 = fmadds(fpr0, fpr6, fpr1)     fpr0 = m[3][2]
//   fpr4 = fpr4 + fpr5                  fpr2 = fpr2 + fpr3
//   fpr0 = fpr0 + fpr1
//
// Behaviour contract
//   inputs  : matrix = r3, source = r4, dest = r5
//   reads   : read_f32 of the fifteen elements in the translated body's order
//             (matrix+16, source+4, matrix+20, matrix+24, matrix+0,
//              source+0, matrix+4, matrix+8, matrix+32, source+8, matrix+36,
//              matrix+40, matrix+48, matrix+52, matrix+56)
//   writes  : write_f32(dest + 0), write_f32(dest + 4), write_f32(dest + 8)
//   result  : none (the guest function returns void)
//   registers written by the translated body: fpr0..fpr7 and ps1 0..7 (the
//             `lfs`/`stfs` pairs update the paired-single mirror).  The native
//             path reproduces all of them.
//   guest instructions: 31
// ---------------------------------------------------------------------------
struct MatrixTransformResult {
  double fpr[8];   // f0..f7
};

__attribute__((always_inline)) inline MatrixTransformResult matrix_transform(GuestMem mem, uint64_t& inst_count,
                                               uint32_t matrix, uint32_t source,
                                               uint32_t dest) {
  double f[8];
  // The guest's register allocation, in the translated body's order.  Every
  // read and every operation is in the same place as in the translated body,
  // so the ordered effect log and the result are identical.
  f[3] = mem.read_f32(matrix + 16u);
  f[2] = mem.read_f32(source + 4u);
  f[1] = mem.read_f32(matrix + 20u);
  f[5] = guest_fmuls(static_cast<float>(f[3]), static_cast<float>(f[2]));
  f[0] = mem.read_f32(matrix + 24u);
  f[3] = guest_fmuls(static_cast<float>(f[1]), static_cast<float>(f[2]));
  f[4] = mem.read_f32(matrix + 0u);
  f[1] = guest_fmuls(static_cast<float>(f[0]), static_cast<float>(f[2]));
  f[7] = mem.read_f32(source + 0u);
  f[2] = mem.read_f32(matrix + 4u);
  f[5] = guest_fmadds(static_cast<float>(f[4]), static_cast<float>(f[7]),
                      static_cast<float>(f[5]));
  f[0] = mem.read_f32(matrix + 8u);
  f[3] = guest_fmadds(static_cast<float>(f[2]), static_cast<float>(f[7]),
                      static_cast<float>(f[3]));
  f[4] = mem.read_f32(matrix + 32u);
  f[1] = guest_fmadds(static_cast<float>(f[0]), static_cast<float>(f[7]),
                      static_cast<float>(f[1]));
  f[6] = mem.read_f32(source + 8u);
  f[2] = mem.read_f32(matrix + 36u);
  f[0] = mem.read_f32(matrix + 40u);
  f[5] = guest_fmadds(static_cast<float>(f[4]), static_cast<float>(f[6]),
                      static_cast<float>(f[5]));
  f[4] = mem.read_f32(matrix + 48u);
  f[3] = guest_fmadds(static_cast<float>(f[2]), static_cast<float>(f[6]),
                      static_cast<float>(f[3]));
  f[2] = mem.read_f32(matrix + 52u);
  f[1] = guest_fmadds(static_cast<float>(f[0]), static_cast<float>(f[6]),
                      static_cast<float>(f[1]));
  f[0] = mem.read_f32(matrix + 56u);
  f[4] = guest_fadds(static_cast<float>(f[4]), static_cast<float>(f[5]));
  f[2] = guest_fadds(static_cast<float>(f[2]), static_cast<float>(f[3]));
  f[0] = guest_fadds(static_cast<float>(f[0]), static_cast<float>(f[1]));
  // stfs f4, 0(r5) ; stfs f2, 4(r5) ; stfs f0, 8(r5)
  mem.write_f32(dest + 0u, static_cast<float>(f[4]));
  mem.write_f32(dest + 4u, static_cast<float>(f[2]));
  mem.write_f32(dest + 8u, static_cast<float>(f[0]));
  inst_count += 31;   // 15 lfs, 12 arithmetic, 3 stfs, blr
  MatrixTransformResult r;
  for (int i = 0; i < 8; ++i) r.fpr[i] = f[i];
  return r;
}

// ---------------------------------------------------------------------------
// 0x80286E28 - RadicalMathLibrary::Matrix::RotateVector
//               (src/master_match/c_80286e28.cpp)
//
//   void Matrix::RotateVector(const Vector& src, Vector* dest) const
//   {
//     float x,y,z;
//     x = m[0][0]*src.x + m[1][0]*src.y + m[2][0]*src.z;
//     y = m[0][1]*src.x + m[1][1]*src.y + m[2][1]*src.z;
//     z = m[0][2]*src.x + m[1][2]*src.y + m[2][2]*src.z;
//     dest->Set(x,y,z);
//   }
//
// The rotation of a vector: the same three rows of the matrix, without the
// translation.  The register allocation and the order of the operations are
// the translated body's.
//
// Behaviour contract
//   inputs  : matrix = r3, source = r4, dest = r5
//   reads   : read_f32 of the twelve elements in the translated body's order
//             (matrix+16, source+4, matrix+20, matrix+24, matrix+0,
//              source+0, matrix+4, matrix+8, matrix+32, source+8, matrix+36,
//              matrix+40)
//   writes  : write_f32(dest + 0), write_f32(dest + 4), write_f32(dest + 8)
//   result  : none (the guest function returns void)
//   registers written by the translated body: fpr0..fpr7 and ps1 0..7.  The
//             native path reproduces all of them.
//   guest instructions: 25
// ---------------------------------------------------------------------------
__attribute__((always_inline)) inline MatrixTransformResult matrix_rotate_vector(GuestMem mem, uint64_t& inst_count,
                                                  uint32_t matrix, uint32_t source,
                                                  uint32_t dest) {
  double f[8];
  f[3] = mem.read_f32(matrix + 16u);
  f[2] = mem.read_f32(source + 4u);
  f[1] = mem.read_f32(matrix + 20u);
  f[5] = guest_fmuls(static_cast<float>(f[3]), static_cast<float>(f[2]));
  f[0] = mem.read_f32(matrix + 24u);
  f[3] = guest_fmuls(static_cast<float>(f[1]), static_cast<float>(f[2]));
  f[4] = mem.read_f32(matrix + 0u);
  f[1] = guest_fmuls(static_cast<float>(f[0]), static_cast<float>(f[2]));
  f[7] = mem.read_f32(source + 0u);
  f[6] = guest_fmadds(static_cast<float>(f[4]), static_cast<float>(f[7]),
                      static_cast<float>(f[5]));
  f[2] = mem.read_f32(matrix + 4u);
  f[0] = mem.read_f32(matrix + 8u);
  f[3] = guest_fmadds(static_cast<float>(f[2]), static_cast<float>(f[7]),
                      static_cast<float>(f[3]));
  f[5] = mem.read_f32(matrix + 32u);
  f[1] = guest_fmadds(static_cast<float>(f[0]), static_cast<float>(f[7]),
                      static_cast<float>(f[1]));
  f[4] = mem.read_f32(source + 8u);
  f[2] = mem.read_f32(matrix + 36u);
  f[0] = mem.read_f32(matrix + 40u);
  f[5] = guest_fmadds(static_cast<float>(f[5]), static_cast<float>(f[4]),
                      static_cast<float>(f[6]));
  f[2] = guest_fmadds(static_cast<float>(f[2]), static_cast<float>(f[4]),
                      static_cast<float>(f[3]));
  f[0] = guest_fmadds(static_cast<float>(f[0]), static_cast<float>(f[4]),
                      static_cast<float>(f[1]));
  mem.write_f32(dest + 0u, static_cast<float>(f[5]));
  mem.write_f32(dest + 4u, static_cast<float>(f[2]));
  mem.write_f32(dest + 8u, static_cast<float>(f[0]));
  inst_count += 25;   // 12 lfs, 9 arithmetic, 3 stfs, blr
  MatrixTransformResult r;
  for (int i = 0; i < 8; ++i) r.fpr[i] = f[i];
  return r;
}

// ---------------------------------------------------------------------------
// 0x8028A914 and 0x8028A974 - RadicalMathLibrary::VectorX::Transform
//               (src/master_match/c_8028a914.cpp, src/master_match/c_8028a974.cpp)
//
//   void VectorX::Transform(const Matrix& m)
//   {
//     Vector tmp;
//     m.Transform(*this, &tmp);
//     x = tmp.x;  y = tmp.y;  z = tmp.z;
//   }
//
// This is the "small connected group" of the assignment: a vector operation
// that calls a matrix helper.  The translated body
//
//   * builds a 32 byte frame,
//   * calls 0x80286D08 (Matrix::Transform) with the matrix, `this` and the
//     frame slot at frame+8 as the destination,
//   * copies the three results out of that frame slot back into `this`.
//
// One kernel replaces the whole group.  The intermediate vector never leaves
// the host registers: the kernel computes it once, publishes it to the frame
// slot (the guest's `stfs` writes, which the effect log and the guest RAM
// comparison both see) and reads it back from there (the guest's `lfs`
// reads) - but the arithmetic uses the host registers, and the guest call,
// the callee's dispatch, the callee's register traffic and the callee's
// instruction accounting all disappear.
//
// Equivalence with the two separate lowerings: the two-kernel form would
// publish the callee's result to the frame slot and the caller would reload
// it; the one-kernel form computes the same values and performs the same
// memory effects in the same order.  The observable difference between the
// two forms is the register file the callee leaves behind, and the group
// kernel publishes exactly the values the translated callee leaves behind
// (f0..f7 and their paired-single mirrors), so the register file is
// identical too.
//
// Behaviour contract
//   inputs  : sp = r1, this = r3, matrix = r4
//   reads   : read32(frame + 28) and read32(frame + 36) at the end, plus the
//             fifteen `lfs` of the matrix transform and the three `lfs` of the
//             frame slot
//   writes  : write32(frame, sp), write32(frame + 36, lr), write32(frame + 28,
//             r31), the three `stfs` of the matrix transform into the frame
//             slot and the three `stfs` of the copy-back into `this`
//   order   : frame, lr, r31, the matrix transform's reads, the matrix
//             transform's writes, the copy-back's read/write pairs, the two
//             frame reloads - exactly the translated body's order
//   result  : none (the guest function returns void)
//   registers written by the translated body: fpr0 (the last value copied
//             out of the frame slot), fpr1..fpr7 and ps1 0..7 (the callee's
//             scratch), gpr0 (the restored link register), gpr3 (the matrix),
//             gpr4 (`this`), gpr5 (the frame slot address).  gpr1 (the stack
//             pointer) and gpr31 (the callee-saved register) keep their entry
//             values in both paths.
//   guest instructions: 51 (20 of the caller and 31 of the callee)
// ---------------------------------------------------------------------------
struct VectorTransformResult {
  double fpr[8];        // f0..f7
  uint32_t gpr0;        // r0: the restored link register
  uint32_t gpr3;        // r3: the matrix
  uint32_t gpr4;        // r4: `this`
  uint32_t gpr5;        // r5: the address of the frame slot
};

inline VectorTransformResult vector_x_transform(GuestMem mem, uint64_t& inst_count,
                                                 uint32_t sp, uint32_t this_ptr,
                                                 uint32_t matrix, uint32_t link_register,
                                                 uint32_t r31) {
  // stwu r1, -32(r1) : the frame is at sp-32
  const uint32_t frame = sp - 32u;
  // stw r0, 36(r1)  (the link register) and stw r31, 28(r1) (the callee
  // saved register) - the guest spills both before the call.
  mem.write32(frame, sp);
  mem.write32(frame + 36u, link_register);
  mem.write32(frame + 28u, r31);
  inst_count += 20;                   // the caller's own instructions

  // The callee: m.Transform(*this, &tmp) with &tmp = frame + 8.
  const MatrixTransformResult m = matrix_transform(mem, inst_count, matrix, this_ptr,
                                                   frame + 8u);

  // lfs f0, 8(r1) ; stfs f0, 0(r31) - and the two following pairs
  const float x = mem.read_f32(frame + 8u);
  mem.write_f32(this_ptr + 0u, x);
  const float y = mem.read_f32(frame + 12u);
  mem.write_f32(this_ptr + 4u, y);
  const float z = mem.read_f32(frame + 16u);
  mem.write_f32(this_ptr + 8u, z);
  // lwz r31, 28(r1) ; lwz r0, 36(r1)
  mem.read32(frame + 28u);
  mem.read32(frame + 36u);

  VectorTransformResult r;
  // f0 holds the last value copied out of the frame slot, f1..f7 are the
  // callee's scratch registers.
  r.fpr[0] = static_cast<double>(z);
  for (int i = 1; i < 8; ++i) r.fpr[i] = m.fpr[i];
  r.gpr0 = link_register;
  r.gpr3 = matrix;
  r.gpr4 = this_ptr;
  r.gpr5 = frame + 8u;
  return r;
}

// ---------------------------------------------------------------------------
// 0x8028A974 - the rotation twin of 0x8028A914
//
// The guest's 0x8028A974 is the same wrapper as 0x8028A914, but it calls
// 0x80286E28 (RadicalMathLibrary::Matrix::RotateVector) instead of
// 0x80286D08 (Matrix::Transform): it rotates the vector without the
// translation.
//
// The recovered source src/master_match/c_8028a974.cpp describes
// VectorX::Transform (the translating variant) and does NOT match the
// translated body of 0x8028A974, so the semantics of this kernel are inferred
// from the translated code; the arithmetic itself comes from the recovered
// Matrix::RotateVector source (src/master_match/c_80286e28.cpp).
//
// Behaviour contract: the same as 0x8028A914, with the rotation's twelve
// reads and the rotation's 25 guest instructions (45 in total).
// ---------------------------------------------------------------------------
inline VectorTransformResult vector_rotate(GuestMem mem, uint64_t& inst_count,
                                            uint32_t sp, uint32_t this_ptr,
                                            uint32_t matrix, uint32_t link_register,
                                            uint32_t r31) {
  const uint32_t frame = sp - 32u;
  mem.write32(frame, sp);
  mem.write32(frame + 36u, link_register);
  mem.write32(frame + 28u, r31);
  inst_count += 20;                   // the caller's own instructions

  const MatrixTransformResult m = matrix_rotate_vector(mem, inst_count, matrix,
                                                       this_ptr, frame + 8u);

  const float x = mem.read_f32(frame + 8u);
  mem.write_f32(this_ptr + 0u, x);
  const float y = mem.read_f32(frame + 12u);
  mem.write_f32(this_ptr + 4u, y);
  const float z = mem.read_f32(frame + 16u);
  mem.write_f32(this_ptr + 8u, z);
  mem.read32(frame + 28u);
  mem.read32(frame + 36u);

  VectorTransformResult r;
  r.fpr[0] = static_cast<double>(z);
  for (int i = 1; i < 8; ++i) r.fpr[i] = m.fpr[i];
  r.gpr0 = link_register;
  r.gpr3 = matrix;
  r.gpr4 = this_ptr;
  r.gpr5 = frame + 8u;
  return r;
}

// ---------------------------------------------------------------------------
// 0x803A2CC8 - atan (src/external/petari/MSL_C/MSL_Common_Embedded/Math/
//                     Double_precision/s_atan.c)
//
//   double atan(double x)
//   {
//     ... the fdlibm argument reduction and the odd polynomial ...
//   }
//
// `s_atan.c` is the only self-contained guest mathematics function in the
// capture: its coefficient tables live in the guest's read-only data (the
// translated body reaches them with `lis r5, 0x8048; addi r5, r5, 0x5C38`,
// so atanhi is at 0x80485C38, atanlo at 0x80485C58 and aT at 0x80485C78) and
// its four literals are in the guest's small data area (r2 - 15128 .. -15096).
// The kernel therefore reads every coefficient from guest memory and never
// carries a host copy of the table.
//
// The guest spills its argument to its frame to read the argument's bit
// pattern (`stfd f1, 8(r1)` followed by `lwz r6, 8(r1)` and, on the NaN
// path, `lwz r0, 12(r1)`), so the native path reproduces that frame traffic
// exactly: the store and the two loads are guest memory effects the ordered
// effect log and the guest RAM comparison both see.
//
// The argument reduction, the polynomial and the final combination are
// transcribed from the translated body, operation by operation, so the result
// is bit identical.
//
// Behaviour contract
//   inputs  : sda = r2, sp = r1, x = f1
//   reads   : read32(sp - 8) (the argument's high word), read32(sp - 4) on the
//             NaN path, and the coefficient table reads of the path taken
//   writes  : write32(sp - 16, sp) and write_f64(sp - 8, x) - the frame
//             traffic of the translated body
//   result  : f1
//   registers written by the translated body: fpr0..fpr13 (the scratch
//             registers of the argument reduction and the polynomial), cr0
//             (the flags of the last comparison), gpr0, gpr3, gpr4, gpr5,
//             gpr6.  The native path reproduces gpr0, gpr3, gpr4, gpr5 and
//             gpr6; fpr0, fpr2..fpr13 and cr0 stay EABI-volatile and are not
//             read by the call sites the lowering serves.
//   guest instructions: 14 (NaN), 21 (the tiny argument), 23 (|x| large),
//             26 (infinity), 55 (the id = -1 reduction), 73/75 (id = 0),
//             70/72 (id = 1), 68/70 (id = 2 and id = 3).  The odd count of
//             the last four is the sign of the argument: the negative path
//             takes the `fneg` branch, which retires two more instructions.
// ---------------------------------------------------------------------------
// The guest registers the atan lowering writes back.
//
// The kernel writes them through the references, because the guest writes r3
// only on some of its paths (the NaN path and the early return leave it
// alone) and because the value of r3 has to survive unchanged on those paths.
inline void atan_double(GuestMem mem, uint64_t& inst_count, uint32_t sda, uint32_t sp,
                        double x, double& fpr1, uint32_t& gpr0, uint32_t& gpr3,
                        uint32_t& gpr4, uint32_t& gpr5, uint32_t& gpr6) {
  // The guest's frame: stwu r1, -16(r1) ; stfd f1, 8(r1)
  mem.write32(sp - 16u, sp);
  mem.write_f64(sp - 8u, x);
  // lwz r6, 8(r1) - the high word of the argument
  const uint32_t hi = mem.read32(sp - 8u);
  const uint32_t ix = hi & 0x7FFFFFFFu;
  // The base of the coefficient tables: lis r5, 0x8048 ; addi r5, r5, 0x5C38
  const uint32_t table = 0x80485C38u;
  const uint32_t atanhi = table;          // r5 + 0
  const uint32_t atanlo = table + 32u;     // r5 + 32
  const uint32_t aT = table + 64u;         // r5 + 64

  // The registers the guest has written so far.
  gpr4 = ix;      // rlwinm r4, r6, 0, 1, 31
  gpr5 = table;   // lis r5, 0x8048 ; addi r5, r5, 0x5C38
  gpr6 = hi;      // lwz r6, 8(r1)

  if (ix >= 0x44100000u) {
    // |x| is large: the result is +-pi/2.
    if (ix > 0x7FF00000u) {
      // NaN: the guest returns x + x.
      gpr0 = 0x7FF00000u;
      fpr1 = x + x;
      inst_count += 14;
      return;
    }
    if (ix == 0x7FF00000u) {
      // lwz r0, 12(r1) - the low word of the argument
      const uint32_t lo = mem.read32(sp - 4u);
      gpr0 = lo;
      if (lo != 0u) {
        // NaN: the guest returns x + x.
        fpr1 = x + x;
        inst_count += 14;
        return;
      }
    } else {
      // addis r0, r4, 0x7FF0
      gpr0 = ix - 0x7FF00000u;
    }
    // The guest's two table loads: atanhi[3] and atanlo[3].
    const double hi3 = mem.read_f64(atanhi + 24u);
    const double lo3 = mem.read_f64(atanlo + 24u);
    gpr3 = atanlo;   // addi r3, r5, 32
    gpr4 = atanhi;   // addi r4, r5, 0
    if (static_cast<int32_t>(hi) > 0) {
      fpr1 = hi3 + lo3;
    } else {
      fpr1 = -hi3 - lo3;
    }
    inst_count += (ix == 0x7FF00000u) ? 26u : 23u;
    return;
  }

  int32_t id = -1;
  double v = x;
  if (ix < 0x3FDC0000u) {
    if (ix < 0x3E200000u) {
      // The guest compares 1.0e300 + x against 1.0.
      const double big = mem.read_f64(sda - 15128u);
      const double one = mem.read_f64(sda - 15120u);
      if (big + x > one) {
        // The guest returns x unchanged.
        gpr0 = 0x3E200000u;
        fpr1 = x;
        inst_count += 21;
        return;
      }
    }
    gpr0 = 0xFFFFFFFFu;   // li r0, -1
  } else {
    const double a = std::fabs(x);
    if (ix < 0x3FF30000u) {
      if (ix < 0x3FE60000u) {
        // id = 0 : x = (2.0 * x - 1.0) / (2.0 + x)
        const double two = mem.read_f64(sda - 15112u);
        const double one = mem.read_f64(sda - 15120u);
        v = (two * a - one) / (two + a);
        mem.write_f64(sp - 8u, v);   // stfd f1, 8(r1)
        id = 0;
        gpr0 = 0u;
      } else {
        // id = 1 : x = (x - 1.0) / (x + 1.0)
        const double one = mem.read_f64(sda - 15120u);
        v = (a - one) / (one + a);
        mem.write_f64(sp - 8u, v);   // stfd f1, 8(r1)
        id = 1;
        gpr0 = 8u;
      }
    } else if (ix < 0x40038000u) {
      // id = 2 : x = (x - 1.5) / (1.0 + 1.5 * x)
      const double onehalf = mem.read_f64(sda - 15104u);
      const double one = mem.read_f64(sda - 15120u);
      v = (a - onehalf) / (one + onehalf * a);
      mem.write_f64(sp - 8u, v);   // stfd f1, 8(r1)
      id = 2;
      gpr0 = 16u;
    } else {
      // id = 3 : x = -1.0 / x
      const double minus_one = mem.read_f64(sda - 15096u);
      v = minus_one / a;
      mem.write_f64(sp - 8u, v);   // stfd f1, 8(r1)
      id = 3;
      gpr0 = 24u;
    }
  }

  // The polynomial.  The guest reads the eleven coefficients in this order.
  const double a10 = mem.read_f64(aT + 80u);
  const double a9 = mem.read_f64(aT + 72u);
  const double a8 = mem.read_f64(aT + 64u);
  const double a7 = mem.read_f64(aT + 56u);
  const double a6 = mem.read_f64(aT + 48u);
  const double a5 = mem.read_f64(aT + 40u);
  const double a4 = mem.read_f64(aT + 32u);
  const double a3 = mem.read_f64(aT + 24u);
  const double a2 = mem.read_f64(aT + 16u);
  const double a1 = mem.read_f64(aT + 8u);
  const double a0 = mem.read_f64(aT + 0u);
  gpr3 = aT;    // addi r3, r5, 64
  const double z = v * v;
  const double w = z * z;
  const double s1 = z * (a0 + w * (a2 + w * (a4 + w * (a6 + w * (a8 + w * a10)))));
  const double s2 = w * (a1 + w * (a3 + w * (a5 + w * (a7 + w * a9))));

  if (id < 0) {
    fpr1 = v - v * (s1 + s2);
    inst_count += 55;
    return;
  }

  // atanlo[id] and atanhi[id]
  const double lo = mem.read_f64(atanlo + static_cast<uint32_t>(id) * 8u);
  const double hi_v = mem.read_f64(atanhi + static_cast<uint32_t>(id) * 8u);
  gpr3 = atanlo;   // addi r3, r5, 32
  gpr4 = atanhi;   // addi r4, r5, 0
  double result = hi_v - ((v * (s1 + s2) - lo) - v);
  if (static_cast<int32_t>(hi) < 0) result = -result;
  fpr1 = result;
  inst_count += (id == 0) ? 73u : (id == 1) ? 70u : (id == 2) ? 73u : 68u;
  if (static_cast<int32_t>(hi) < 0) inst_count += 2u;
}

// ---------------------------------------------------------------------------
// Entry helpers.
//
// Each helper publishes exactly the guest state the replaced translated body
// produced: the result register, the retired instruction count and the
// return address.  The staged wrapper keeps the renamed translated body for
// every other entry (interior entry, traced call, disabled lowering).
// ---------------------------------------------------------------------------

inline void apply_indexed_int_load(CPUContext& ctx) {
  const IndexedIntLoadResult r =
      indexed_int_load(guest_mem(ctx), ctx.inst_count, ctx.gpr[3], ctx.gpr[4]);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.value;
  ctx.pc = ctx.lr;
}

inline void apply_sda2_global_swap(CPUContext& ctx) {
  const Sda2GlobalSwapResult r =
      sda2_global_swap(guest_mem(ctx), ctx.inst_count, ctx.gpr[13], ctx.gpr[3]);
  ctx.gpr[0] = r.gpr0;
  ctx.gpr[3] = r.value;
  ctx.pc = ctx.lr;
}

inline void apply_fill_translate(CPUContext& ctx) {
  const FillTranslateResult r =
      fill_translate(guest_mem(ctx), ctx.inst_count, ctx.gpr[3], ctx.gpr[4]);
  for (int i = 0; i < 3; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.fpr[i];
  }
  ctx.pc = ctx.lr;
}

inline void apply_matrix_transform(CPUContext& ctx) {
  const MatrixTransformResult r = matrix_transform(guest_mem(ctx), ctx.inst_count,
                                                    ctx.gpr[3], ctx.gpr[4], ctx.gpr[5]);
  for (int i = 0; i < 8; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.fpr[i];
  }
  ctx.pc = ctx.lr;
}

inline void apply_vector_x_transform(CPUContext& ctx) {
  const VectorTransformResult r = vector_x_transform(guest_mem(ctx), ctx.inst_count,
                                                      ctx.gpr[1], ctx.gpr[3], ctx.gpr[4],
                                                      ctx.lr, ctx.gpr[31]);
  for (int i = 0; i < 8; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.fpr[i];
  }
  ctx.gpr[0] = ctx.lr;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.pc = ctx.lr;
}

inline void apply_vector_rotate(CPUContext& ctx) {
  const VectorTransformResult r = vector_rotate(guest_mem(ctx), ctx.inst_count,
                                                 ctx.gpr[1], ctx.gpr[3], ctx.gpr[4],
                                                 ctx.lr, ctx.gpr[31]);
  for (int i = 0; i < 8; ++i) {
    ctx.fpr[i] = r.fpr[i];
    ctx.ps1[i] = r.fpr[i];
  }
  ctx.gpr[0] = ctx.lr;
  ctx.gpr[3] = r.gpr3;
  ctx.gpr[4] = r.gpr4;
  ctx.gpr[5] = r.gpr5;
  ctx.pc = ctx.lr;
}

inline void apply_atan_double(CPUContext& ctx) {
  atan_double(guest_mem(ctx), ctx.inst_count, ctx.gpr[2], ctx.gpr[1], ctx.fpr[1],
              ctx.fpr[1], ctx.gpr[0], ctx.gpr[3], ctx.gpr[4], ctx.gpr[5], ctx.gpr[6]);
  ctx.pc = ctx.lr;
}

}  // namespace nwii::runtime::native_lower
