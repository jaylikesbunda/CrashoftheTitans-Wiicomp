// NATIVE_LLVM_IR_V1: C-ABI support shims for clang-compiled LLVM-IR guest slices.
//
// native-port/compiler/emit_llvm_ir.py emits every IR guest function as
//
//     define void @func_8023B080(ptr %ctx)
//
// The exact x86-64 Windows calling convention of the generated
// `func_8023B080(NWII_CTX_PARAM ctx)` (functions.h declares it `extern "C"`,
// so the symbol is the plain `func_8023B080`, and the CPUContext& reference
// travels in RCX as a pointer).  The IR object can therefore replace the C++
// definition with no bridge and no ABI mismatch.
// The IR itself never names a C++ symbol: it reaches the runtime only through
// the `extern "C"` entry points declared at the top of the emitted module.
// This TU (built by the ordinary MinGW g++ toolchain, so no libstdc++ object
// has to cross the C++ ABI) supplies them by forwarding to the real runtime.
//
// It is added to CrashNative only by tools/fix_llvm_ir_backend.py (marker
// # NATIVE_LLVM_IR_V1); the C++-only build never links it.
#include "runtime/cpu_context.h"

extern "C" void nwii_ir_interpret_step(nwii::runtime::CPUContext* ctx) {
    nwii::runtime::interpret_step(*ctx);
}

extern "C" uint32_t nwii_ir_read32_slow(nwii::runtime::CPUContext* ctx, uint32_t addr) {
    return ctx->mmu.native_read32_slow(addr);
}

extern "C" void nwii_ir_write32_slow(nwii::runtime::CPUContext* ctx, uint32_t addr, uint32_t value) {
    ctx->mmu.native_write32_slow(addr, value);
}

extern "C" void nwii_ir_gx_wgpipe_write32(uint32_t value) {
    nwii::runtime::GX_WGPIPE_Write32(value);
}
