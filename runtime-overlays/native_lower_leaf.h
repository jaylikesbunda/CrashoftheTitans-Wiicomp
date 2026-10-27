#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Source-backed native lowerings for the guest functions of the Revolution
// SDK's operating-system layer and the pure helper leaves.
//
// Every function here was recovered in this repository and its translated
// form is executed by the native port. The native implementation replaces
// the translated body; the behaviour contract of each function is stated
// above it.
//
// The rules the implementations follow are in native_lower_support.h:
// guest memory effects go through `GuestMem`, guest addresses are 32-bit and
// wrap, and no guest global is duplicated on the host.
#include "native_lower_support.h"

namespace nwii::runtime::native_lower {

// ---------------------------------------------------------------------------
// OSClearContext - 0x803B5988
//
// Recovered source: src/external/ogws/revolution/OS/OSContext.c
//
//     void OSClearContext(OSContext* ctx) {
//         ctx->mode = 0;
//         ctx->state = 0;
//         if (ctx == OS_CURRENT_FPU_CONTEXT) {
//             OS_CURRENT_FPU_CONTEXT = NULL;
//         }
//     }
//
// The recovered source names the two context fields and the comparison; the
// translated guest code supplies the exact guest layout and the address of
// the OS_CURRENT_FPU_CONTEXT global:
//
//   * `mode` is the 16-bit field at ctx + 0x1A0 and `state` the 16-bit field
//     at ctx + 0x1A2 (`sth r5, 0x1A0(r3)` / `sth r5, 0x1A2(r3)`),
//   * OS_CURRENT_FPU_CONTEXT is the 32-bit word at 0x800000D8; the translated
//     code materialises it as `lis r4, 0x8000` and uses `r4 + 0xD8`,
//   * the value written is r5, which the prologue sets to zero.
//
// Contract:
//   inputs   ctx        the OSContext pointer (guest address)
//   memory   writes 0 (u16) at ctx + 0x1A0
//            writes 0 (u16) at ctx + 0x1A2
//            reads the u32 at 0x800000D8
//            writes 0 (u32) at 0x800000D8 when the read value equals ctx
//   order    the two context writes happen before the global is read
//   result   none (the guest function returns void)
//   guest registers
//            r4 holds 0x80000000 and r5 holds 0 on return; the staged entry
//            republishes both because two call sites (0x803B7C18 and
//            0x803BB88C) read them after the call. r0 and CR0 are left alone:
//            no call site reads them.
//   inst_count
//            the translated path retires 7 instructions when the global is not
//            cleared and 9 when it is (the clear adds the store and the
//            return).
// ---------------------------------------------------------------------------
inline constexpr uint32_t kOsCurrentFpuContext = 0x800000D8u;

inline void os_clear_context(GuestMem mem, uint64_t& inst_count, uint32_t context) {
  mem.write16(context + 0x1A0u, 0);
  mem.write16(context + 0x1A2u, 0);
  const uint32_t current = mem.read32(kOsCurrentFpuContext);
  inst_count += 7;                        // li, lis, sth, sth, lwz, cmpw, beq
  if (current == context) {
    mem.write32(kOsCurrentFpuContext, 0);
    inst_count += 2;                      // stw, blr
  }
}

}  // namespace nwii::runtime::native_lower
