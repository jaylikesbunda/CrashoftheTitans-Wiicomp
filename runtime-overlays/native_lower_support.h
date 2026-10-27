#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Shared support for source-backed native lowering of translated guest
// functions.
//
// A "native lowering" replaces one translated guest function (produced by the
// PPC->C++ translator) with a host implementation that is written from the
// function's recovered C/C++ source. The lowering is only allowed to be
// adopted when its observable behaviour is identical to the translated
// function, so this header fixes the rules that every lowering follows:
//
//  * Guest memory effects go through the runtime MMU (`GuestMem`), never
//    through host pointers. That keeps watchpoints, MMIO, FIFO writes,
//    command ordering and publication identical to the translated path.
//  * Guest addresses are 32-bit and wrap, so all address arithmetic is done
//    in `uint32_t` and the guest's own base registers (r2 = SDA, r13 = SDA2)
//    are passed in as arguments. No guest global is duplicated on the host.
//  * The FPU state, the guest's condition register and the guest's integer
//    registers are only touched where the original function touches them.
//
// The PowerPC EABI makes r0-r3, f0-f13 and CR0 volatile across a call. A
// lowering therefore only has to reproduce the registers the original
// function *restores* or *redefines* in a way a caller can observe; the
// qualification records the volatile set it does not reproduce and proves
// that no call site reads it (see tools/check_native_lowering.py).
#include "runtime/cpu_context.h"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cmath>

namespace nwii::runtime::native_lower {

// The differential-test build defines NATIVE_LOWERING_TRACE_MEMORY so the
// native path records the same ordered memory effects the translated path
// records. The production build does not define it, so the recording costs
// nothing at run time.
#ifdef NATIVE_LOWERING_TRACE_MEMORY
namespace native_lower_detail {
void trace(uint32_t address, int width, uint64_t value, bool is_write);
}
#define NATIVE_LOWERING_TRACE(address, width, value, is_write) \
  ::nwii::runtime::native_lower::native_lower_detail::trace((address), (width), (value), (is_write))
#else
#define NATIVE_LOWERING_TRACE(address, width, value, is_write) ((void)0)
#endif

// ---------------------------------------------------------------------------
// Narrow guest memory interface.
//
// Every access uses the runtime MMU, so the watch, MMIO and FIFO semantics of
// the translated path are preserved exactly. The MMU already masks addresses
// to 32 bits, which reproduces the guest's address wrapping.
// ---------------------------------------------------------------------------
struct GuestMem {
  MMU& mmu;

  explicit GuestMem(MMU& m) : mmu(m) {}

  inline uint8_t  read8 (uint32_t a) const {
    uint8_t v = mmu.read8(a);
    NATIVE_LOWERING_TRACE(a, 1, v, false);
    return v;
  }
  inline uint16_t read16(uint32_t a) const {
    uint16_t v = mmu.read16(a);
    NATIVE_LOWERING_TRACE(a, 2, v, false);
    return v;
  }
  inline uint32_t read32(uint32_t a) const {
    uint32_t v = mmu.read32(a);
    NATIVE_LOWERING_TRACE(a, 4, v, false);
    return v;
  }
  inline uint64_t read64(uint32_t a) const {
    uint64_t v = mmu.read64(a);
    NATIVE_LOWERING_TRACE(a, 8, v, false);
    return v;
  }
  inline float    read_f32(uint32_t a) const {
    float v = mmu.read_f32(a);
    uint32_t bits; std::memcpy(&bits, &v, 4);
    NATIVE_LOWERING_TRACE(a, 4, bits, false);
    return v;
  }
  inline double   read_f64(uint32_t a) const {
    double v = mmu.read_f64(a);
    uint64_t bits; std::memcpy(&bits, &v, 8);
    NATIVE_LOWERING_TRACE(a, 8, bits, false);
    return v;
  }

  inline void write8 (uint32_t a, uint8_t  v) { mmu.write8(a, v);  NATIVE_LOWERING_TRACE(a, 1, v, true); }
  inline void write16(uint32_t a, uint16_t v) { mmu.write16(a, v); NATIVE_LOWERING_TRACE(a, 2, v, true); }
  inline void write32(uint32_t a, uint32_t v) { mmu.write32(a, v); NATIVE_LOWERING_TRACE(a, 4, v, true); }
  inline void write64(uint32_t a, uint64_t v) { mmu.write64(a, v); NATIVE_LOWERING_TRACE(a, 8, v, true); }
  inline void write_f32(uint32_t a, float   v) {
    mmu.write_f32(a, v);
    uint32_t bits; std::memcpy(&bits, &v, 4);
    NATIVE_LOWERING_TRACE(a, 4, bits, true);
  }
  inline void write_f64(uint32_t a, double  v) {
    mmu.write_f64(a, v);
    uint64_t bits; std::memcpy(&bits, &v, 8);
    NATIVE_LOWERING_TRACE(a, 8, bits, true);
  }
};

// ---------------------------------------------------------------------------
// Guest hardware ports.
//
// 0xCC008000 is the BP (XF) register write port. The runtime maps the whole
// 0x0C008xxx page onto the write-gather pipe (GX_WGPIPE_Write*).
// 0xCC000000 is the CP command FIFO write port (HW_Reg_Write32 -> hw_cp).
// ---------------------------------------------------------------------------
namespace port {
inline constexpr uint32_t kBpRegister = 0xCC008000u;
inline constexpr uint32_t kCommandFifo = 0xCC000000u;
}

// ---------------------------------------------------------------------------
// Guest small data area.
//
// The guest keeps the base of its small data area in r2 (SDA) and r13 (SDA2).
// A lowering receives the base it needs; every global it touches is read from
// and written to guest memory, so there is exactly one copy of guest state.
// ---------------------------------------------------------------------------
inline uint32_t sda_word(GuestMem& mem, uint32_t sda, int32_t offset) {
  return mem.read32(sda + static_cast<uint32_t>(offset));
}

// ---------------------------------------------------------------------------
// Adapter helpers.
//
// A native lowering replaces one guest function. Its shape is fixed:
//
//   Result impl(GuestMem mem, uint64_t& inst_count, <typed arguments>);
//
//  * the guest's argument registers arrive as typed parameters, so the
//    caller's values stay in host registers;
//  * every guest memory effect goes through `mem`;
//  * `inst_count` receives the number of guest instructions the native path
//    replaces, so the runtime's instruction counter (used by the timebase
//    cache window and by the performance census) keeps the value the
//    translated path produced;
//  * the returned value is the guest's result register value.
//
// The staged lowering rewrites a guest function definition into
//
//   NWII_GUEST_ABI void func_<addr>(NWII_CTX_PARAM ctx) {
//     if (<entry conditions>) { <native call>; ctx.pc = ctx.lr; return; }
//     native_original_<addr>(ctx);
//   }
//
// The entry conditions are the ordinary entry test, the tracing test, the kill
// switch and the candidate's own guards (for example the guest's GQR state).
// The original body is kept verbatim under the `native_original_` name, so an
// interior entry, a traced call, a disabled lowering or a candidate whose
// guard does not hold keeps the original behaviour.
// ---------------------------------------------------------------------------

// The kill switch: set NWII_NO_NATIVE_LOWERING to force every lowering onto
// the translated path. The environment is read once, so the test is a load and
// a branch.
inline bool disabled() {
  static const bool value = std::getenv("NWII_NO_NATIVE_LOWERING") != nullptr;
  return value;
}

// The conditions every native entry shares: the guest must not be traced (a
// traced call has to reach the translated body so the trace stays identical)
// and the lowering must not be disabled.
#define NWII_LOWERING_ENTRY_CONDITIONS \
  "!nwii::runtime::g_trace_calls && !nwii::runtime::native_lower::disabled()"

inline GuestMem guest_mem(CPUContext& ctx) { return GuestMem(ctx.mmu); }

}  // namespace nwii::runtime::native_lower
