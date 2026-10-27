// Hardware stubs shared by the arena tests. Every observable side effect appends to a log so two runs can be compared.
#pragma once
#include "runtime/cpu_context.h"
#include <vector>
#include <cstdio>
#include <csetjmp>
#include <windows.h>
namespace nwii::runtime {
MMU* g_mmu = nullptr;
CPUContext* g_ctx_ptr = nullptr;
bool g_trace_calls = false;
uint32_t g_watch_addr = 0;
int32_t g_native_poll_credit = 1 << 30;
bool g_native_fifo_ok = std::getenv("FIFO_OK") != nullptr;
struct Event { uint8_t kind; uint32_t addr; uint64_t value; };
std::vector<Event> g_log;
uint64_t g_observations = 0;
jmp_buf* g_longjmp_target = nullptr;
uint32_t g_longjmp_on_addr = 0xffffffffu;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) {}
static inline void ev(uint8_t k, uint32_t a, uint64_t v) { g_log.push_back({k, a, v}); ++g_observations; }
void watch_hit(uint32_t a, uint32_t v, int w) { ev(1, a, (uint64_t(w) << 32) | v); }
void GX_WGPIPE_Write8(uint8_t v) { ev(2, 1, v); }
void GX_WGPIPE_Write16(uint16_t v) { ev(2, 2, v); }
void GX_WGPIPE_Write32(uint32_t v) { ev(2, 4, v); }
void GX_WGPIPE_WriteF32(float f) { uint32_t b; std::memcpy(&b, &f, 4); ev(2, 4, b); }
void GX_WGPIPE_WriteF64(double d) { uint64_t b; std::memcpy(&b, &d, 8); ev(2, 8, b); }
uint16_t HW_Reg_Read16(uint32_t a) { ev(3, a, 2); return uint16_t(a * 2654435761u >> 16); }
uint32_t HW_Reg_Read32(uint32_t a) { ev(3, a, 4); return a ^ 0xa5c39921u; }
void HW_Reg_Write16(uint32_t a, uint16_t v) { ev(4, a, v | (2ull << 32)); if (g_longjmp_target && a == g_longjmp_on_addr) std::longjmp(*g_longjmp_target, 1); }
void HW_Reg_Write32(uint32_t a, uint32_t v) { ev(4, a, v | (4ull << 32)); if (g_longjmp_target && a == g_longjmp_on_addr) std::longjmp(*g_longjmp_target, 1); }
}

static LONG WINAPI crash_filter(EXCEPTION_POINTERS* ep) {
  std::fprintf(stderr, "CRASH code=%08lx rip=%p addr=%p rsp=%p%c", ep->ExceptionRecord->ExceptionCode, (void*)ep->ContextRecord->Rip,
               ep->ExceptionRecord->NumberParameters > 1 ? (void*)ep->ExceptionRecord->ExceptionInformation[1] : nullptr, (void*)ep->ContextRecord->Rsp, 10);
  std::fflush(stderr);
  return EXCEPTION_EXECUTE_HANDLER;
}
static const int crash_filter_installed = (SetUnhandledExceptionFilter(crash_filter), 0);
