#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Source-backed native lowering of closed guest computations.
//
// Every function in this header replaces the *translated* body of one guest
// function with a host implementation written from that function's recovered
// C source (src/c/*.c).  The rules every lowering follows are the ones of
// native_lower_support.h:
//
//   * Guest memory effects go through GuestMem, so the runtime MMU keeps
//     watchpoints, MMIO, FIFO writes, command ordering and publication
//     identical to the translated path.
//   * Guest addresses are 32 bit and wrap: every address computation is done
//     in uint32_t.
//   * No host copy of a guest global: the guest's own small-data-area base
//     (r13 = SDA2) is passed in as an argument and the global is read from
//     (and written to) guest memory.
//   * Guest layout (struct offsets, bitfields) is taken from the translated
//     guest code, never from a host sizeof/offsetof.
//
// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------
//
// A lowering has the fixed shape of native_lower_support.h:
//
//   Result impl(GuestMem mem, uint64_t& inst_count, <typed arguments>);
//
//   * the guest's argument registers arrive as typed parameters;
//   * every guest memory effect goes through `mem`;
//   * `inst_count` receives the number of guest instructions the native path
//     replaces, so the runtime's instruction counter keeps the value the
//     translated path produced.  The counter is guest visible: with the
//     default (virtual) clock CPUContext::read_timebase() *is* inst_count.
//   * the returned value is the value the guest body leaves in r3.
//
// The staged entry (tools/native_lowering.py) publishes the result register
// and the return address:
//
//   NWII_GUEST_ABI void func_802B6CF4(NWII_CTX_PARAM ctx) {
//     if (ctx.pc == 0x802B6CF4) {
//       ctx.gpr[3] = native_lower::range_equal_probe(native_lower::guest_mem(ctx),
//                                                    ctx.inst_count,
//                                                    ctx.gpr[3], ctx.gpr[4]);
//       ctx.pc = ctx.lr; return;
//     }
//     native_original_802B6CF4(ctx);
//   }
//
// The apply_* helpers at the end of this header are that entry body, ready to
// be called by the staged wrapper.
//
// ---------------------------------------------------------------------------
// Registers the native path does not reproduce
// ---------------------------------------------------------------------------
//
// The translated bodies also write scratch registers and CR0 (gpr[0], gpr[4],
// gpr[5], gpr[8], cr[0]).  The native path does not write them:
//
//   * gpr[0] is r0, volatile in the PowerPC EABI.
//   * gpr[4] / gpr[5] / gpr[8] are r4 / r5 / r8, volatile in the PowerPC EABI
//     (r0-r12 are volatile across a call).
//   * cr[0] is CR0, volatile in the PowerPC EABI.
//
// No call site of any lowered function reads those registers after the call
// (tools/check_native_lowering.py proves it statically over the whole
// generated tree).  Every other guest register, the condition register fields
// other than CR0, LR, CTR, XER, MSR and FPSCR are left exactly as the
// translated body left them.

#if __has_include("native_lower_support.h")
#include "native_lower_support.h"
#else
#include "../runtime-overlays/native_lower_support.h"
#endif

#include <bit>
#include <cstdint>

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// 0x802B6CF4 - FUN_802b6cf4 (src/c/c_802b6cf4.c)
//
//   unsigned int FUN_802b6cf4(unsigned int *param_1,unsigned int *param_2)
//   {
//     if (param_1[1] != param_2[1]) {
//       return 0;
//     }
//     uVar1 = __cntlzw(*param_2 - *param_1);
//     return uVar1 >> 5;
//   }
//
// Behaviour contract
//   inputs  : a = r3, b = r4 (guest addresses of two 8-byte ranges)
//   reads   : read32(a+4), read32(b+4); when they are equal additionally
//             read32(a+0) and read32(b+0), in that order
//   writes  : none
//   result  : 1 when a[1] == b[1] and a[0] == b[0], otherwise 0
//             (cntlzw(diff) >> 5 is 1 exactly when diff == 0)
//   registers written by the translated body: gpr[0], gpr[3], gpr[5], cr[0].
//             The native path reproduces only gpr[3]; gpr[0], gpr[5] and
//             cr[0] are EABI-volatile and are not read by any call site.
//   guest instructions: 6 when a[1] != b[1], 9 when a[1] == b[1].
// ---------------------------------------------------------------------------
inline uint32_t range_equal_probe(GuestMem mem, uint64_t& inst_count, uint32_t a, uint32_t b) {
  const uint32_t a_hi = mem.read32(a + 4u);
  const uint32_t b_hi = mem.read32(b + 4u);
  if (a_hi != b_hi) {
    // li r3, 0 ; blr
    inst_count += 6;
    return 0u;
  }
  const uint32_t a_lo = mem.read32(a + 0u);
  const uint32_t b_lo = mem.read32(b + 0u);
  // subf r0, r3, r0  ->  b_lo - a_lo (mod 2^32)
  const uint32_t diff = b_lo - a_lo;
  // cntlzw r0, r0 ; rlwinm r3, r0, 27, 5, 31
  //   cntlzw(diff) is 32 for diff == 0 and 0..31 otherwise, so
  //   rotl(cntlzw, 27) & 0x07FFFFFF == (cntlzw >> 5) & 0x07FFFFFF,
  //   which is 1 for diff == 0 and 0 otherwise.
  const uint32_t leading = diff == 0u ? 32u : static_cast<uint32_t>(std::countl_zero(diff));
  inst_count += 9;
  return (leading >> 5) & 0x07FFFFFFu;
}

// ---------------------------------------------------------------------------
// 0x80231DFC - FUN_80231dfc (src/c/c_80231dfc.c)
//
//   int FUN_80231dfc(unsigned int *param_1)
//   {
//     if (*param_1 >> 0x10 == 0xffff) return 0;
//     return *(int *)(*(int *)&sda2_80596fe0 + 0x20) + (*param_1 >> 0x10) * 8;
//   }
//
// Behaviour contract
//   inputs  : sda2 = r13 (the guest's small data area 2 base), key = r3
//   reads   : read32(key+0)
//             when ((v >> 16) & 0xFFFF) != 0xFFFF additionally
//             read32(sda2 - 12544)   (the SDA2 slot that holds the table
//                                     pointer; 0x80596FE0 for the game's
//                                     SDA2 base) and then read32(ptr + 32)
//             (the table base), in that order
//   writes  : none
//   result  : 0 when the top half of *key is 0xFFFF, otherwise
//             table_base + index * 8 (mod 2^32) with index = (*key >> 16)
//   registers written by the translated body: gpr[0], gpr[3], cr[0].
//             gpr[0] and cr[0] are EABI-volatile and are not read by any
//             call site.
//   guest instructions: 6 on the early return, 8 otherwise.
// ---------------------------------------------------------------------------
inline uint32_t sda2_table_lookup(GuestMem mem, uint64_t& inst_count, uint32_t sda2, uint32_t key) {
  const uint32_t word = mem.read32(key + 0u);
  // rlwinm r0, r0, 16, 16, 31 -> the top half of the word
  const uint32_t index = (word >> 16) & 0xFFFFu;
  if (index == 0xFFFFu) {
    // li r3, 0 ; blr
    inst_count += 6;
    return 0u;
  }
  // lwz r3, -12544(r13) : the SDA2 slot that holds the table pointer
  const uint32_t table_holder = mem.read32(sda2 - 12544u);
  // rlwinm r0, r0, 3, 0, 28 -> index * 8
  const uint32_t offset = (index << 3) & 0xFFFFFFF8u;
  // lwz r3, 32(r3) : the table base
  const uint32_t table_base = mem.read32(table_holder + 32u);
  // add r3, r3, r0
  inst_count += 8;
  return table_base + offset;
}

// ---------------------------------------------------------------------------
// 0x800D5370 - FUN_800d5370 (src/c/c_800d5370.c)
//
//   int FUN_800d5370(register int param_1,register int param_2)
//   {
//     if (param_2 == -1) {
//       return 0xffffffff;
//     }
//     return *(int *)(param_1 + param_2 * 4 + 0x43e0);
//   }
//
// Behaviour contract
//   inputs  : table = r3, index = r4
//   reads   : read32(table + index*4 + 0x43E0), skipped when index == -1
//   writes  : none
//   result  : 0xFFFFFFFF when index == -1, otherwise the loaded word
//   registers written by the translated body: gpr[0], gpr[3], cr[0].
//             gpr[0] and cr[0] are EABI-volatile and are not read by any
//             call site.
//   guest instructions: 4 for the index == -1 path, 5 otherwise.
// ---------------------------------------------------------------------------
inline uint32_t indexed_slot_load(GuestMem mem, uint64_t& inst_count, uint32_t table, uint32_t index) {
  if (index == 0xFFFFFFFFu) {
    // li r3, -1 ; blr
    inst_count += 4;
    return 0xFFFFFFFFu;
  }
  // rlwinm r0, r4, 2, 0, 29 -> index * 4 (mod 2^32)
  const uint32_t scaled = (index << 2) & 0xFFFFFFFCu;
  // add r3, r3, r0 ; lwz r3, 0x43E0(r3)
  const uint32_t address = table + scaled + 17376u;
  inst_count += 5;
  return mem.read32(address);
}

// ---------------------------------------------------------------------------
// 0x80321E38 - FUN_80321e38 (src/c/c_80321e38.c)
//
//   unsigned char FUN_80321e38(register int param_1)
//   {
//     return *(unsigned char *)(*(int *)(*(int *)(*(int *)(*(int *)(param_1 + 0x98)
//              + 0x14) + *(int *)(param_1 + 0x40) * 4) + 0x10) + 10) >> 5 & 1;
//   }
//
// Behaviour contract
//   inputs  : object = r3
//   reads   : read32(object+0x98), read32(object+0x40),
//             read32(node+0x14), read32(node+index*4), read32(leaf+0x10),
//             read8(leaf+10) - in exactly that order
//   writes  : none
//   result  : bit 5 of the loaded byte (0 or 1)
//   registers written by the translated body: gpr[0], gpr[3], gpr[4].
//             gpr[0] and gpr[4] are EABI-volatile and are not read by any
//             call site.
//   guest instructions: 9 (the body has no branch).
// ---------------------------------------------------------------------------
inline uint32_t object_flag_bit(GuestMem mem, uint64_t& inst_count, uint32_t object) {
  const uint32_t node = mem.read32(object + 0x98u);
  const uint32_t index = mem.read32(object + 0x40u);
  const uint32_t slot = mem.read32(node + 0x14u);
  // rlwinm r0, r0, 2, 0, 29 -> index * 4 (mod 2^32)
  const uint32_t scaled = (index << 2) & 0xFFFFFFFCu;
  const uint32_t entry = mem.read32(slot + scaled);
  const uint32_t leaf = mem.read32(entry + 0x10u);
  const uint32_t flags = mem.read8(leaf + 10u);
  // rlwinm r3, r0, 27, 31, 31 -> bit 5 of the byte
  inst_count += 9;
  return (flags >> 5) & 1u;
}

// ---------------------------------------------------------------------------
// 0x80292364 - FUN_80292364 (src/c/c_80292364.c)
//
//   void FUN_80292364(register int param_1,char param_2,char param_3,char param_4,
//                      char param_5)
//   {
//     *(char *)(*(int *)(param_1 + 0x4c) + 0x18) = param_2;
//     *(char *)(*(int *)(param_1 + 0x4c) + 0x19) = param_3;
//     *(char *)(*(int *)(param_1 + 0x4c) + 0x1a) = param_4;
//     *(char *)(*(int *)(param_1 + 0x4c) + 0x1b) = param_5;
//     return;
//   }
//
// Behaviour contract
//   inputs  : object = r3, b0..b3 = r4..r7 (only the low byte of each is used)
//   reads   : read32(object+0x4C) four times, once before each store.  The
//             pointer is re-read on purpose: a store may alias the pointer
//             slot itself, and the original body observes that.
//   writes  : write8(ptr+0x18, b0), write8(ptr+0x19, b1), write8(ptr+0x1A, b2),
//             write8(ptr+0x1B, b3) - interleaved with the reads, in that order
//   result  : the guest function returns void; the body still leaves the
//             last pointer it loaded in r3, and the native path reproduces
//             that value so the register file stays identical
//   registers written by the translated body: gpr[3], gpr[4], gpr[8].  gpr[4]
//             and gpr[8] are EABI-volatile and are not read by any call site.
//   guest instructions: 9 (the body has no branch).
// ---------------------------------------------------------------------------
inline uint32_t store_byte_quad(GuestMem mem, uint64_t& inst_count, uint32_t object,
                                 uint8_t b0, uint8_t b1, uint8_t b2, uint8_t b3) {
  // lwz r8, 0x4C(r3) ; stb r4, 0x18(r8)
  uint32_t ptr = mem.read32(object + 76u);
  mem.write8(ptr + 24u, b0);
  // lwz r4, 0x4C(r3) ; stb r5, 0x19(r4)
  ptr = mem.read32(object + 76u);
  mem.write8(ptr + 25u, b1);
  // lwz r4, 0x4C(r3) ; stb r6, 0x1A(r4)
  ptr = mem.read32(object + 76u);
  mem.write8(ptr + 26u, b2);
  // lwz r3, 0x4C(r3) ; stb r7, 0x1B(r3)
  ptr = mem.read32(object + 76u);
  mem.write8(ptr + 27u, b3);
  inst_count += 9;
  return ptr;
}

// ---------------------------------------------------------------------------
// 0x8046616C - FUN_8046616c (src/c/c_8046616c.c)
//
//   #define DAT_8058f590 (*(unsigned int *)0x8058F590)
//   unsigned int FUN_8046616c(void)
//   {
//     DAT_8058f590 = DAT_8058f590 * 0x19660d + 0x3c6ef35f;
//     return DAT_8058f590 >> 0x10;
//   }
//
// The global lives at the absolute guest address 0x8058F590 (the body reaches
// it with "lis r5, 0x8059 ; lwz/stw -0xA70(r5)"), so it is read from and
// written to guest memory; there is no host copy of it.
//
// Behaviour contract
//   inputs  : none
//   reads   : read32(0x8058F590)
//   writes  : write32(0x8058F590, new_value)
//   result  : (new_value >> 16) & 0xFFFF
//   registers written by the translated body: gpr[0], gpr[3], gpr[4], gpr[5].
//             gpr[0], gpr[4] and gpr[5] are EABI-volatile and are not read
//             by any call site.
//   guest instructions: 10 (the body has no branch).
// ---------------------------------------------------------------------------
inline constexpr uint32_t kLcgStateAddress = 0x8058F590u;

inline uint32_t lcg_step(GuestMem mem, uint64_t& inst_count) {
  const uint32_t state = mem.read32(kLcgStateAddress);
  // mullw r3, r4, r0 (r0 = 0x19660D) ; addis/addi -> + 0x3C6EF35F
  const uint32_t next = state * 0x0019660Du + 0x3C6EF35Fu;
  mem.write32(kLcgStateAddress, next);
  // rlwinm r3, r0, 16, 16, 31 -> the top half of the new state
  inst_count += 10;
  return (next >> 16) & 0xFFFFu;
}

// ---------------------------------------------------------------------------
// Entry helpers.
//
// Each helper publishes exactly the guest state the replaced translated body
// produced: the result register, the retired instruction count and the
// return address.  The staged wrapper keeps the renamed translated body for
// every other entry (interior entry, traced call, disabled lowering).
//
//   NWII_GUEST_ABI void func_802B6CF4(NWII_CTX_PARAM ctx) {
//     if (ctx.pc == 0x802B6CF4) { native_lower::apply_range_equal_probe(ctx); return; }
//     native_original_802B6CF4(ctx);
//   }
// ---------------------------------------------------------------------------
inline void apply_range_equal_probe(CPUContext& ctx) {
  ctx.gpr[3] = range_equal_probe(guest_mem(ctx), ctx.inst_count, ctx.gpr[3], ctx.gpr[4]);
  ctx.pc = ctx.lr;
}

inline void apply_sda2_table_lookup(CPUContext& ctx) {
  ctx.gpr[3] = sda2_table_lookup(guest_mem(ctx), ctx.inst_count, ctx.gpr[13], ctx.gpr[3]);
  ctx.pc = ctx.lr;
}

inline void apply_indexed_slot_load(CPUContext& ctx) {
  ctx.gpr[3] = indexed_slot_load(guest_mem(ctx), ctx.inst_count, ctx.gpr[3], ctx.gpr[4]);
  ctx.pc = ctx.lr;
}

inline void apply_object_flag_bit(CPUContext& ctx) {
  ctx.gpr[3] = object_flag_bit(guest_mem(ctx), ctx.inst_count, ctx.gpr[3]);
  ctx.pc = ctx.lr;
}

inline void apply_store_byte_quad(CPUContext& ctx) {
  ctx.gpr[3] = store_byte_quad(guest_mem(ctx), ctx.inst_count, ctx.gpr[3],
                                static_cast<uint8_t>(ctx.gpr[4]),
                                static_cast<uint8_t>(ctx.gpr[5]),
                                static_cast<uint8_t>(ctx.gpr[6]),
                                static_cast<uint8_t>(ctx.gpr[7]));
  ctx.pc = ctx.lr;
}

inline void apply_lcg_step(CPUContext& ctx) {
  ctx.gpr[3] = lcg_step(guest_mem(ctx), ctx.inst_count);
  ctx.pc = ctx.lr;
}

}  // namespace nwii::runtime::native_lower
