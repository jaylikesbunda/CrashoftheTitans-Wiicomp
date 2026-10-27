#pragma once
#include "runtime/cpu_context.h"
#include "runtime/native_perf_census.h"

inline const bool native_guest_fill_enabled = std::getenv("NWII_NO_GUEST_FILL_BLOCK") == nullptr;
__attribute__((always_inline)) static inline bool native_guest_fill_block(nwii::runtime::MMU& mmu, uint32_t address, uint32_t value) {
    native_perf_census::GuestAdd(native_perf_census::FillRequests);
    if (!native_guest_fill_enabled || nwii::runtime::g_trace_calls) return false;
    const uint32_t physical = address & 0x3fffffffu;
    if ((physical & 65535u) > 65504u || value != (value & 255u) * 0x01010101u ||
        nwii::runtime::native_watch_span_may_hit(physical, 32u)) return false;
    const intptr_t bias = mmu.page_bias[physical >> 16];
    if (bias <= 1) return false;
    std::memset(reinterpret_cast<void*>(bias + physical), int(value & 255u), 32u);
    native_perf_census::GuestAdd(native_perf_census::FillFast);
    return true;
}
